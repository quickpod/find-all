#!/usr/bin/env python3
r"""FindAll entry point (built into FindAll.exe). GUI with no args, CLI with args."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Single-instance marker: the installer's AppMutex checks this to warn the
# user to close the app before install/uninstall. Harmless off Windows.
if os.name == "nt":
    try:
        import ctypes
        ctypes.windll.kernel32.CreateMutexW(None, False, "QuickOpen.FindAll")
    except Exception:
        pass



def main():
    argv = sys.argv[1:]
    if argv:
        from findall import __main__ as cli
        if hasattr(cli, 'main'):
            try:
                return cli.main(argv)
            except TypeError:
                sys.argv = ['findall', *argv]; return cli.main()
        sys.argv = ['findall', *argv]
        import runpy; runpy.run_module('findall', run_name='__main__'); return 0
    from findall import gui
    return gui.main() or 0


if __name__ == '__main__':
    sys.exit(main() or 0)
