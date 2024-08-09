import json
import os
from functools import wraps
import click
import docker
import shutil



ONEOFF_CLI_CONFIG_PATH = "~/.oneoff_cli/config"
ONEOFF_CLI_TMP_PATH = "~/.oneoff_cli/tmp"


def store_configuration(config) -> None:
    """Stores oneoff cli configuration

    Returns:
        None
    """
    try:
        os.makedirs(
            os.path.dirname(os.path.expanduser(ONEOFF_CLI_CONFIG_PATH)), exist_ok=True
        )
        with open(os.path.expanduser(ONEOFF_CLI_CONFIG_PATH), "w") as file:
            json.dump(config, file, indent=2)
    except:
        click.secho(
            "Something went wrong when trying to store the configuration",
            fg="red",
        )
        raise
    return None


def get_configuration() -> object:
    """Gets the current persisted configuration

    Returns:
        object: Configuration data, or None
    """

    try:
        with open(os.path.expanduser(ONEOFF_CLI_CONFIG_PATH), "r") as file:
            data = json.load(file)
            return data
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        click.secho(
            "Something went wrong when trying to read the configuration file...",
            fg="red",
        )

        return None


# Define a simple configuration check function
def is_configured() -> bool:
    conf = get_configuration()
    if conf:
        return True
    return False


# Decorator to ensure configuration is set
def require_cli_config(func):
    """Decorator for ensuring that there exists configuration for the CLI"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        if is_configured():
            # CLI config exists, call the original function
            return func(*args, **kwargs)
        else:
            # No config found
            click.secho(
                "It appears you haven't configured the CLI. Run 'oneoff configure' ",
                fg="red",
            )

    return wrapper


def docker_is_running():
    click.echo("Verifying Docker is running...")
    try:
        client = docker.from_env()
        client.ping()
        return True
    except docker.errors.DockerException as e:
        return False
    
def get_current_directory():
    return os.path.abspath(os.getcwd())

def get_absolute_path_if_exists(filename):
    """Get the absolute path of a file located in the current directory if it exists; otherwise, return None."""
    current_directory = get_current_directory()
    file_path = os.path.join(current_directory, filename)
    
    if os.path.exists(file_path):
        return os.path.abspath(file_path)
    else:
        return None
    
def create_temp_dockerfile(requirements_path, script_path, script):
    """Create a temporary Dockerfile."""

    os.makedirs(
        os.path.dirname(os.path.expanduser(ONEOFF_CLI_TMP_PATH)), exist_ok=True
    )

    if not requirements_path:
        try:

            temp_requirements_path = os.path.join(ONEOFF_CLI_TMP_PATH,"requirements.txt")

            # Write a temporary empty requirements.txt file
            with open(os.path.expanduser(temp_requirements_path), "w") as file:
                file.write("")
        except:
            click.secho("Something went wrong when trying to create a temporary requirements.txt file", fg="red")

    else:
        # Todo copy req file to the temp build location.
        temp_requirements_path = os.path.expanduser(os.path.join(ONEOFF_CLI_TMP_PATH, 'requirements.txt'))
        # Copy the script to the temporary path
        shutil.copy(requirements_path, temp_requirements_path)

        pass

    # Build context will always be in ONEOFF_CLI_TMP_PATH, so the script must be copied there
    # Copy script_path to ONEOFF_CLI_TMP_PATH/script.py
    temp_script_path = os.path.expanduser(os.path.join(ONEOFF_CLI_TMP_PATH, script))
    # Copy the script to the temporary path
    shutil.copy(script_path, temp_script_path)

    dockerfile_content = f"""
    # Use a lightweight Python base image
    FROM python:3.11-alpine

    # Set working directory in the container
    WORKDIR /app

    # Copy requirements.txt to the working directory
    COPY requirements.txt ./requirements.txt

    # Install Python dependencies
    RUN pip install --no-cache-dir -r requirements.txt

    # Copy the rest of your application code
    COPY {script} .

    # Command to run your Python script
    CMD ["python", "{script}"]
    """

    try:
        temp_dockerfile_path = os.path.join(ONEOFF_CLI_TMP_PATH,"Dockerfile")
        with open(os.path.expanduser(temp_dockerfile_path), "w") as file:
            file.write(dockerfile_content)
    except:
        click.secho("Something went wrong when trying to create a temporary Dockerfile", fg="red")

    return temp_dockerfile_path
