import os
import subprocess
import sys


def run_pyinstaller_build():
    sep = ";" if sys.platform.startswith("win") else ":"
    pyinstaller_command = [
        sys.executable, "-m", "PyInstaller",
        "--onedir",
        "--windowed",
        "--icon=icons/app_icon.ico",
        "--name=Writing Tools",
        "--clean",
        "--noconfirm",
        f"--add-data=electron_ui/index.html{sep}electron_ui",
        f"--add-data=electron_ui/toast.html{sep}electron_ui",
        f"--add-data=icons{sep}icons",
        f"--add-data=options.json{sep}.",
        # Exclude unnecessary modules
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "IPython",
        "--exclude-module", "jedi",
        "--exclude-module", "email_validator",
        "--exclude-module", "psutil",
        "--exclude-module", "pyzmq",
        "--exclude-module", "tornado",
        # Exclude unused PySide6 modules while keeping QtWebEngineCore & QtWebEngineWidgets
        "--exclude-module", "PySide6.QtXml",
        "--exclude-module", "PySide6.QtSql",
        "--exclude-module", "PySide6.QtTest",
        "--exclude-module", "PySide6.QtHelp",
        "--exclude-module", "PySide6.QtMultimedia",
        "--exclude-module", "PySide6.QtMultimediaWidgets",
        "--exclude-module", "PySide6.QtLocation",
        "--exclude-module", "PySide6.QtSerialPort",
        "--exclude-module", "PySide6.QtBluetooth",
        "--exclude-module", "PySide6.QtNfc",
        "--exclude-module", "PySide6.QtCharts",
        "--exclude-module", "PySide6.QtDataVisualization",
        "--exclude-module", "PySide6.QtPdf",
        "--exclude-module", "PySide6.QtPdfWidgets",
        "--exclude-module", "PySide6.QtQuick3D",
        "--exclude-module", "PySide6.QtSensors",
        "--exclude-module", "PySide6.Qt3DCore",
        "--exclude-module", "PySide6.Qt3DRender",
        "--exclude-module", "PySide6.Qt3DInput",
        "--exclude-module", "PySide6.Qt3DLogic",
        "--exclude-module", "PySide6.Qt3DAnimation",
        "--exclude-module", "PySide6.Qt3DExtras",
        "main.py"
    ]

    try:
        if os.path.exists('dist'):
            os.system("rmdir /s /q dist" if sys.platform.startswith("win") else "rm -rf dist")
        if os.path.exists('build'):
            os.system("rmdir /s /q build" if sys.platform.startswith("win") else "rm -rf build")

        subprocess.run(pyinstaller_command, check=True)
        print("Build completed successfully! Output in dist/Writing Tools/")
    except subprocess.CalledProcessError as e:
        print(f"Build failed with error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    run_pyinstaller_build()