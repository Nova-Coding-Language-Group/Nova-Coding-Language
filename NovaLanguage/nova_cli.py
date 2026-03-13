#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import webbrowser

def _get_icon_path():
    base = sys._MEIPASS if hasattr(sys, '_MEIPASS') else os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "NovaLanguageIcon.ico")

def _hide_console():
    """Hide the console window (used when double-clicked to open GUI)."""
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE = 0
    except Exception:
        pass

def _was_double_clicked():
    """
    Detect if launched by double-click vs from a terminal.
    When launched from cmd/powershell, the parent process is cmd.exe/powershell.exe.
    When double-clicked, the parent is explorer.exe.
    """
    try:
        import ctypes

        # Get parent process ID
        current_pid = os.getpid()
        
        # Use NtQueryInformationProcess to get parent PID
        class PROCESS_BASIC_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("Reserved1",       ctypes.c_void_p),
                ("PebBaseAddress",  ctypes.c_void_p),
                ("Reserved2",       ctypes.c_void_p * 2),
                ("UniqueProcessId", ctypes.c_ulong),
                ("ParentProcessId", ctypes.c_ulong),
            ]

        pbi = PROCESS_BASIC_INFORMATION()
        ret_len = ctypes.c_ulong()
        handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, current_pid)
        ctypes.windll.ntdll.NtQueryInformationProcess(
            handle, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), ctypes.byref(ret_len)
        )
        ctypes.windll.kernel32.CloseHandle(handle)
        parent_pid = pbi.ParentProcessId

        # Get parent process name
        h = ctypes.windll.kernel32.OpenProcess(0x0410, False, parent_pid)
        buf = ctypes.create_unicode_buffer(260)
        size = ctypes.c_ulong(260)
        ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        ctypes.windll.kernel32.CloseHandle(h)
        parent_name = os.path.basename(buf.value).lower()

        # Explorer = double-clicked, anything else = terminal
        return parent_name in ("explorer.exe", "")
    except Exception:
        # If detection fails, check if no args were passed
        return len(sys.argv) == 1

def read_version():
    base = sys._MEIPASS if hasattr(sys, '_MEIPASS') else os.path.dirname(os.path.abspath(__file__))
    pkg_path = os.path.join(base, "NovaVSCodeExtension", "package.json")
    try:
        with open(pkg_path, "r", encoding="utf-8") as f:
            return json.load(f).get("version", "0.0.1")
    except Exception:
        return "0.1.0-dev"

VERSION = read_version()

HELP_TEXT = f"""Nova v{VERSION} - Nova Language CLI

Usage:
  Nova <file.nova>            Run a .nova file with the interpreter
  Nova <file.nova> --compile  Compile a .nova file to an exe
  Nova <file.nova> --type dll Compile to a dll instead
  Nova --version              Show version and logo
  Nova Help                   Show this message
  Nova Site                   Open the Nova website
  Nova Git                    Open the Nova GitHub repository

  (Double-click Nova.exe to open the GUI compiler)
"""

def print_logo():
    print(rf"""
    _   _
   | \ | | ___ __   ____ _  |
   |  \| |/ _ \\ \ / / _` | |
   | |\  | (_) |\ V / (_| | |
   |_| \_|\___/  \_/ \__,_| v{VERSION}
    """)

def open_compiler():
    _hide_console()
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NovaLang.Nova.Compiler.1")
    except Exception:
        pass
    try:
        import tkinter as tk
        import nova_compiler
        root = tk.Tk()
        ico = _get_icon_path()
        if os.path.exists(ico):
            root.iconbitmap(ico)
        nova_compiler.NovaCompilerApp(root)
        root.mainloop()
    except Exception as e:
        print(f"Error opening compiler: {e}")
        sys.exit(1)

def run_interpreter(path):
    try:
        import nova_interpreter
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
        nova_interpreter.Interpreter().run(src)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

def compile_file(path, out_type="exe"):
    try:
        import nova_compiler
    except ImportError:
        print("Error: nova_compiler module not found.")
        sys.exit(1)

    src_path = os.path.abspath(path)
    if not os.path.exists(src_path):
        print(f"Error: File '{path}' not found.")
        sys.exit(1)

    out_base = os.path.splitext(os.path.basename(src_path))[0]
    print(f"Nova v{VERSION} -> Compiling {os.path.basename(src_path)}...")

    try:
        with open(src_path, "r", encoding="utf-8") as f:
            src_code = f.read()

        emitter, _ = nova_compiler.translate_nova_to_il(src_code, out_base)
        il_content = emitter.get_il(emitter._main_lines)

        il_path = os.path.join(os.path.dirname(src_path), f"{out_base}.il")
        with open(il_path, "w", encoding="utf-8") as f:
            f.write(il_content)

        ilasm = nova_compiler.find_ilasm_path()
        if ilasm:
            out_ext = ".exe" if out_type == "exe" else ".dll"
            out_file = os.path.join(os.path.dirname(src_path), out_base + out_ext)
            result = subprocess.run(
                [ilasm, il_path, f"/{out_type}", f"/output={out_file}"],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                print(f"Success! Created {os.path.basename(out_file)}")
            else:
                print("Assembler Error:")
                print(result.stdout)
        else:
            print(f"Generated IL at {il_path}. (ilasm.exe not found - install .NET SDK)")

    except Exception as e:
        print(f"Compilation error: {e}")
        sys.exit(1)

def main():
    args = sys.argv[1:]

    # No args — check if double-clicked or typed "Nova" in terminal
    if not args:
        if _was_double_clicked():
            open_compiler()
        else:
            print(HELP_TEXT)
        return

    cmd = args[0].lower()

    if cmd in ("--version", "-v"):
        print_logo()

    elif cmd in ("help", "--help", "-h"):
        print(HELP_TEXT)

    elif cmd == "site":
        webbrowser.open("https://nova-coding-language-group.github.io/Nova-Website/")
        print("Opening Nova website...")

    elif cmd == "git":
        webbrowser.open("https://github.com/Nova-Coding-Language-Group/Nova-Coding-Language")
        print("Opening Nova GitHub repository...")

    elif cmd.endswith(".nova") or os.path.isfile(args[0]):
        file_path = args[0]
        remaining = args[1:]
        out_type = "exe"
        if "--type" in remaining:
            ti = remaining.index("--type")
            if ti + 1 < len(remaining):
                out_type = remaining[ti + 1]
        if "--compile" in remaining:
            compile_file(file_path, out_type)
        else:
            run_interpreter(file_path)

    else:
        print(f"Unknown command: '{args[0]}'")
        print("Type `Nova Help` to see all commands.")

if __name__ == "__main__":
    main()
