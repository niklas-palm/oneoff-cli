import json
import os
from functools import wraps
import click
import docker
import shutil

ONEOFF_CLI_CONFIG_PATH = os.path.expanduser("~/.oneoff_cli/config")
ONEOFF_CLI_TMP_PATH = os.path.expanduser("~/.oneoff_cli/tmp")


def store_configuration(config: dict) -> None:
    """
    Stores the Oneoff CLI configuration.

    :param config: Configuration data to store.
    :return: None
    """
    try:
        os.makedirs(os.path.dirname(ONEOFF_CLI_CONFIG_PATH), exist_ok=True)
        with open(ONEOFF_CLI_CONFIG_PATH, "w") as file:
            json.dump(config, file, indent=2)
    except Exception:
        click.secho("Error: Failed to store the configuration.", fg="red")
        raise


def get_configuration() -> object:
    """
    Retrieves the current persisted configuration.

    :return: Configuration data, or None if not found.
    """
    try:
        with open(ONEOFF_CLI_CONFIG_PATH, "r") as file:
            return json.load(file)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        click.secho("Error: Failed to read the configuration file.", fg="red")
        return None


def is_configured() -> bool:
    """
    Checks if the CLI is configured.

    :return: True if configured, False otherwise.
    """
    return get_configuration() is not None


def require_cli_config(func):
    """
    Decorator to ensure that CLI configuration exists before proceeding.

    :param func: Function to wrap.
    :return: Wrapped function.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        if is_configured():
            return func(*args, **kwargs)
        else:
            click.secho(
                "It appears you haven't configured the CLI. Run 'oneoff configure'.",
                fg="red",
            )
    return wrapper


def docker_is_running() -> bool:
    """
    Checks if Docker is running.

    :return: True if Docker is running, False otherwise.
    """
    click.echo("Verifying Docker is running...")
    try:
        client = docker.from_env()
        client.ping()
        return True
    except docker.errors.DockerException:
        return False


def get_current_directory() -> str:
    """
    Retrieves the absolute path of the current working directory.

    :return: Current working directory path.
    """
    return os.path.abspath(os.getcwd())


def get_absolute_path_if_exists(filename: str) -> str:
    """
    Retrieves the absolute path of a file if it exists in the current directory.

    :param filename: The filename to check.
    :return: Absolute path if the file exists, None otherwise.
    """
    file_path = os.path.join(get_current_directory(), filename)
    return os.path.abspath(file_path) if os.path.exists(file_path) else None


def create_temp_dockerfile(requirements_path: str, script_path: str, script: str) -> str:
    """
    Creates a temporary Dockerfile for the specified script.

    :param requirements_path: Path to the requirements file.
    :param script_path: Path to the script.
    :param script: Script filename.
    :return: Path to the temporary Dockerfile.
    """
    os.makedirs(ONEOFF_CLI_TMP_PATH, exist_ok=True)

    temp_requirements_path = os.path.join(ONEOFF_CLI_TMP_PATH, "requirements.txt")
    temp_script_path = os.path.join(ONEOFF_CLI_TMP_PATH, script)

    try:
        if requirements_path:
            shutil.copy(requirements_path, temp_requirements_path)
        else:
            with open(temp_requirements_path, "w") as file:
                file.write("")

        shutil.copy(script_path, temp_script_path)

        dockerfile_content = f"""
        FROM python:3.11-alpine

        WORKDIR /app

        COPY requirements.txt ./requirements.txt

        RUN pip install --no-cache-dir -r requirements.txt

        COPY {script} .

        CMD ["python", "{script}"]
        """

        temp_dockerfile_path = os.path.join(ONEOFF_CLI_TMP_PATH, "Dockerfile")
        with open(temp_dockerfile_path, "w") as file:
            file.write(dockerfile_content)

        return temp_dockerfile_path

    except Exception:
        click.secho("Error: Failed to create a temporary Dockerfile.", fg="red")
        raise
