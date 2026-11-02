"""Desktop entry point: .venv/bin/python instantdownloader_app.py"""
if __name__ == "__main__":
    try:
        from instantdownloader.gui import main
    except ModuleNotFoundError as exc:
        raise SystemExit('Install desktop dependencies: python -m pip install -e ".[gui,profiles]"') from exc
    main()
