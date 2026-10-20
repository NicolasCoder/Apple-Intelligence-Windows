import logging
import os
import sys

if sys.platform.startswith("win32"):
    import winreg


class AutostartManager:
    """
    Manages the autostart functionality for Writing Tools on Windows (and Linux XDG autostart).
    Supports both compiled PyInstaller executables AND source/venv installations
    (via a silent windowless VBScript launcher on Windows so no console window flashes on boot).
    """

    REGISTRY_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
    REGISTRY_VALUE_NAME = "WritingTools"

    @staticmethod
    def is_compiled() -> bool:
        """Check if we're running from a compiled exe or source."""
        return hasattr(sys, 'frozen') and hasattr(sys, '_MEIPASS')

    @staticmethod
    def _get_app_dir() -> str:
        """Return the Windows_and_Linux directory containing main.py."""
        if AutostartManager.is_compiled():
            return os.path.dirname(os.path.abspath(sys.executable))
        # This file is in Windows_and_Linux/ui/AutostartManager.py -> parent is Windows_and_Linux
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    @staticmethod
    def ensure_silent_launcher() -> str:
        """
        Create or update `start_silent.vbs` inside `Windows_and_Linux/` so the app can be
        launched on Windows boot (or from shortcuts) with zero console window flashing.
        Returns the absolute path to `start_silent.vbs`.
        """
        app_dir = AutostartManager._get_app_dir()
        vbs_path = os.path.join(app_dir, "start_silent.vbs")
        main_py = os.path.join(app_dir, "main.py")

        python_exe = os.path.abspath(sys.executable)
        pythonw_candidate = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
        runner_exe = pythonw_candidate if os.path.exists(pythonw_candidate) else python_exe
        # Use 1 (SW_SHOWNORMAL) when launching pythonw.exe (which already has no console window)
        # so Windows does NOT set STARTUPINFO.wShowWindow = SW_HIDE (0) on our GUI windows!
        win_style = 1 if runner_exe.lower().endswith("pythonw.exe") else 0

        vbs_content = (
            'Set WshShell = CreateObject("WScript.Shell")\r\n'
            f'WshShell.CurrentDirectory = "{app_dir}"\r\n'
            f'WshShell.Run """{runner_exe}"" ""{main_py}""", {win_style}, False\r\n'
        )
        try:
            with open(vbs_path, "w", encoding="utf-8") as f:
                f.write(vbs_content)
        except Exception as e:
            logging.warning(f"Could not write start_silent.vbs: {e}")
        return vbs_path

    @staticmethod
    def get_startup_path() -> str | None:
        """
        Get the command string that should be registered for Windows autostart.
        Works for both compiled .exe builds and source/venv installations.
        """
        if not sys.platform.startswith('win32'):
            return None

        if AutostartManager.is_compiled():
            return f'"{os.path.abspath(sys.executable)}"'

        vbs_path = AutostartManager.ensure_silent_launcher()
        return f'wscript.exe //B //Nologo "{vbs_path}"'

    @staticmethod
    def set_autostart(enable: bool) -> bool:
        """
        Enable or disable autostart for Writing Tools on Windows (or Linux ~/.config/autostart).
        """
        try:
            if sys.platform.startswith('win32'):
                startup_cmd = AutostartManager.get_startup_path()
                if not startup_cmd:
                    return False

                try:
                    key = winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        AutostartManager.REGISTRY_KEY_PATH,
                        0,
                        winreg.KEY_WRITE
                    )
                    if enable:
                        winreg.SetValueEx(
                            key,
                            AutostartManager.REGISTRY_VALUE_NAME,
                            0,
                            winreg.REG_SZ,
                            startup_cmd
                        )
                    else:
                        try:
                            winreg.DeleteValue(key, AutostartManager.REGISTRY_VALUE_NAME)
                        except WindowsError:
                            pass
                    winreg.CloseKey(key)
                    return True
                except WindowsError as e:
                    logging.error(f"Failed to modify autostart registry: {e}")
                    return False

            # Linux XDG autostart support
            autostart_dir = os.path.expanduser("~/.config/autostart")
            desktop_file = os.path.join(autostart_dir, "writing-tools.desktop")
            if enable:
                os.makedirs(autostart_dir, exist_ok=True)
                app_dir = AutostartManager._get_app_dir()
                main_py = os.path.join(app_dir, "main.py")
                exec_cmd = f'"{sys.executable}" "{main_py}"' if not AutostartManager.is_compiled() else f'"{sys.executable}"'
                content = (
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=Writing Tools\n"
                    f"Exec={exec_cmd}\n"
                    f"Path={app_dir}\n"
                    "X-GNOME-Autostart-enabled=true\n"
                )
                with open(desktop_file, "w", encoding="utf-8") as f:
                    f.write(content)
            else:
                if os.path.exists(desktop_file):
                    os.remove(desktop_file)
            return True

        except Exception as e:
            logging.error(f"Error managing autostart: {e}")
            return False

    @staticmethod
    def check_autostart() -> bool:
        """
        Check if Writing Tools is set to start automatically with Windows/Linux.
        """
        try:
            if sys.platform.startswith('win32'):
                try:
                    key = winreg.OpenKey(
                        winreg.HKEY_CURRENT_USER,
                        AutostartManager.REGISTRY_KEY_PATH,
                        0,
                        winreg.KEY_READ
                    )
                    value, _ = winreg.QueryValueEx(key, AutostartManager.REGISTRY_VALUE_NAME)
                    winreg.CloseKey(key)
                    return bool(value and value.strip())
                except WindowsError:
                    return False

            desktop_file = os.path.expanduser("~/.config/autostart/writing-tools.desktop")
            return os.path.exists(desktop_file)
        except Exception as e:
            logging.error(f"Error checking autostart status: {e}")
            return False