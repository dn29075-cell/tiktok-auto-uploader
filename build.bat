@echo off
title TikTok Uploader V2 - BUILD
echo.
echo  ============================================
echo   TikTok Auto Uploader V2 - BUILD INSTALLER
echo  ============================================
echo.

:: Step 0: Fix icon.ico (phai la file ICO that, khong phai PNG doi ten)
echo [0/3] Kiem tra va fix icon.ico...
python -c "
from PIL import Image
import sys
path = r'K:\AUTO_GEN_AI\APP_AUTO_UPLOAD_V2\electron\assets\icon.ico'
with open(path, 'rb') as f:
    b = f.read(4)
if b == bytes([0,0,1,0]):
    print('icon.ico OK')
else:
    print('Dang convert PNG -> ICO...')
    img = Image.open(path)
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    img.save(path, format='ICO', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])
    print('icon.ico da duoc fix')
" 2>nul || (
    echo [WARN] Khong the kiem tra icon, tiep tuc...
)

:: Step 0b: Deploy 7za wrapper (fix Windows symlink privilege loi)
echo [0/3] Kiem tra 7za wrapper...
set "SEVENZIP_DIR=K:\AUTO_GEN_AI\APP_AUTO_UPLOAD_V2\electron\node_modules\7zip-bin\win\x64"
if not exist "%SEVENZIP_DIR%\7za_orig.exe" (
    echo Dang tao 7za wrapper de fix loi symlink...
    copy "%SEVENZIP_DIR%\7za.exe" "%SEVENZIP_DIR%\7za_orig.exe" > nul

    :: Compile C# wrapper
    echo using System; using System.Diagnostics; using System.IO; using System.Text; > C:\Temp\w.cs
    echo class App { static int Main(string[] args) { string me = System.Reflection.Assembly.GetExecutingAssembly().Location; string orig = Path.Combine(Path.GetDirectoryName(me), "7za_orig.exe"); var sb = new StringBuilder(); for(int i=0;i<args.Length;i++){if(i>0)sb.Append(' ');string a=args[i];if(a.Contains(" "))sb.Append('"').Append(a).Append('"');else sb.Append(a);} var p=new Process();p.StartInfo.FileName=orig;p.StartInfo.Arguments=sb.ToString();p.StartInfo.UseShellExecute=false;p.StartInfo.RedirectStandardError=true;p.Start();string err=p.StandardError.ReadToEnd();p.WaitForExit();if(!string.IsNullOrEmpty(err))Console.Error.Write(err);if(p.ExitCode==2&&err.Contains("symbolic link"))return 0;return p.ExitCode;}} >> C:\Temp\w.cs

    "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:exe /out:"%SEVENZIP_DIR%\7za.exe" "C:\Temp\w.cs" > nul 2>&1
    if exist "%SEVENZIP_DIR%\7za.exe" (
        echo 7za wrapper da duoc cai dat
    ) else (
        copy "%SEVENZIP_DIR%\7za_orig.exe" "%SEVENZIP_DIR%\7za.exe" > nul
        echo Compile that bai, dung 7za goc
    )
) else (
    echo 7za wrapper da co san
)

:: Step 1: Build Python backend -> backend.exe
echo.
echo [1/3] Dong goi Python backend voi PyInstaller...
pip install pyinstaller > nul 2>&1
cd /d K:\AUTO_GEN_AI\APP_AUTO_UPLOAD_V2\backend

pyinstaller api.py ^
  --name backend ^
  --onefile ^
  --noconsole ^
  --add-data "core;core" ^
  --hidden-import uvicorn ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.loops ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.protocols ^
  --hidden-import uvicorn.protocols.http ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.websockets ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  --hidden-import uvicorn.lifespan ^
  --hidden-import uvicorn.lifespan.on ^
  --hidden-import fastapi ^
  --hidden-import pydantic ^
  --hidden-import websockets ^
  --distpath ..\backend-dist ^
  --workpath ..\build-tmp\backend ^
  --specpath ..\build-tmp

if %errorlevel% neq 0 (
  echo.
  echo [FAILED] PyInstaller that bai!
  pause
  exit /b 1
)

echo [OK] backend.exe da tao xong

:: Step 2: Build Electron app + Installer
echo.
echo [2/3] Build Electron installer...
cd /d K:\AUTO_GEN_AI\APP_AUTO_UPLOAD_V2\electron
set CSC_IDENTITY_AUTO_DISCOVERY=false
set WIN_CSC_LINK=
set CSC_LINK=
npm run build:win

if %errorlevel% neq 0 (
  echo.
  echo [FAILED] electron-builder that bai!
  pause
  exit /b 1
)

echo.
echo  ============================================
echo   BUILD THANH CONG!
echo   Installer: dist\TikTok Auto Uploader Setup 2.0.0.exe
echo  ============================================
echo.

:: Open output folder
explorer K:\AUTO_GEN_AI\APP_AUTO_UPLOAD_V2\dist

pause
