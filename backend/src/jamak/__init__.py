"""jamak: local Japanese -> Korean subtitle generator."""

__version__ = "0.1.0"


def main() -> None:
    from jamak.cli import main as cli_main

    cli_main()
