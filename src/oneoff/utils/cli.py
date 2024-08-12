import json
import os
from functools import wraps
import click
import docker
import shutil
import boto3
from botocore.exceptions import ClientError
import time
import click
import os
import docker
import subprocess
import base64
import sys
import json
from datetime import datetime, timezone, timedelta
import re

ONEOFF_CLI_CONFIG_PATH = os.path.expanduser("~/.oneoff_cli/config")
ONEOFF_CLI_JOBS_PATH = os.path.expanduser("~/.oneoff_cli/jobs.txt")
ONEOFF_CLI_TMP_PATH = os.path.expanduser("~/.oneoff_cli/tmp")
CLOUDFORMATION_STACK_NAME = 'oneoff-cli'

cloudformation = boto3.client('cloudformation')

def validate_name(name):
    """
    Validate that the name is in lowercase, contains only hyphens, lowercase letters, and numbers.
    """
    if not name.islower():
        raise ValueError("Name must be in lowercase.")
    if not re.fullmatch(r'[a-z0-9\-]+', name):
        raise ValueError("Name can only contain lowercase letters, numbers, and hyphens.")
    return name



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

def custom_json_encoder(obj):
    """Custom JSON encoder for datetime objects."""
    if isinstance(obj, datetime):
        return obj.isoformat()  # Convert datetime to ISO 8601 format string
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

def custom_json_decoder(job):
    """Custom JSON decoder to convert strings back to datetime objects."""
    if 'created' in job and isinstance(job['created'], str):
        try:
            job['created'] = datetime.fromisoformat(job['created'])
        except ValueError:
            click.secho("Error: Invalid datetime format in jobs file.", fg="red")
    return job

def store_jobs(jobs: list) -> None:
    """
    Stores the jobs, appending them to the existing jobs if any,
    and overwriting jobs with the same 'name'.

    :param jobs: List of jobs to store.
    :return: None
    """
    try:
        # Retrieve existing jobs
        existing_jobs = get_local_jobs() or []
        
        # Create a dictionary from existing jobs with 'name' as the key
        existing_jobs_dict = {job['name']: job for job in existing_jobs}
        
        # Update the dictionary with new jobs, overwriting if 'name' matches
        for job in jobs:
            existing_jobs_dict[job['name']] = job
        
        # Convert the dictionary back to a list
        updated_jobs = list(existing_jobs_dict.values())
        
        # Ensure the directory exists
        os.makedirs(os.path.dirname(ONEOFF_CLI_JOBS_PATH), exist_ok=True)
        
        # Store the updated job list using the custom encoder
        with open(ONEOFF_CLI_JOBS_PATH, "w") as file:
            json.dump(updated_jobs, file, indent=2, default=custom_json_encoder)
    
    except Exception as e:
        click.secho(f"Error: Failed to store the jobs locally. {e}", fg="red")
        raise


def get_local_jobs() -> list:
    """
    Retrieves the jobs from the local file and converts datetime strings back to datetime objects.

    :return: Jobs data as a list of dictionaries.
    """
    try:
        with open(ONEOFF_CLI_JOBS_PATH, "r") as file:
            jobs = json.load(file)
            # Convert datetime strings back to datetime objects
            jobs = [custom_json_decoder(job) for job in jobs]
            return jobs
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        click.secho("Error: Failed to read the jobs file.", fg="red")
        return None
    
def merge_local_with_remote(local_jobs: list, remote_jobs: list) -> list:
    if not remote_jobs:
        # If remote_jobs is empty, set the status of all local jobs to 'STOPPED'
        for job in local_jobs:
            job['status'] = 'STOPPED'
        # Sort the local jobs by 'created' field in reverse order and return
        return sorted(local_jobs, key=lambda job: job['created'], reverse=True)
    
    # Create a dictionary from remote jobs for quick lookup by job_name
    remote_dict = {job['name']: job for job in remote_jobs}
    
    # List to hold the merged jobs
    merged_jobs = []
    
    # Iterate over local jobs
    for local_job in local_jobs:
        job_name = local_job['name']
        if job_name in remote_dict:
            # If job exists in remote, use the remote job's information
            merged_jobs.append(remote_dict.pop(job_name))
        else:
            # If job doesn't exist in remote, set its status to 'STOPPED'
            local_job['status'] = 'STOPPED'
            merged_jobs.append(local_job)
    
    # Add remaining remote jobs that were not in local jobs
    merged_jobs.extend(remote_dict.values())
    
    # Ensure that all jobs are converted to datetime before sorting
    for job in merged_jobs:
        if isinstance(job['created'], str):
            try:
                job['created'] = datetime.fromisoformat(job['created'])
            except ValueError:
                click.secho("Error: Invalid datetime format in jobs file.", fg="red")
                job['created'] = datetime.min  # Set a default value to avoid breaking sorting
        elif not isinstance(job['created'], datetime):
            click.secho(f"Error: Unexpected type for 'created': {type(job['created'])}", fg="red")
            job['created'] = datetime.min  # Set a default value to avoid breaking sorting

    # Sort merged jobs by 'created' field in reverse order
    merged_jobs.sort(key=lambda job: job['created'], reverse=True)
    
    return merged_jobs



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

def get_current_account_id():
    """Fetch the current AWS account ID using boto3."""
    try:
        sts_client = boto3.client('sts')
        response = sts_client.get_caller_identity()
        return response['Account']
    except Exception as e:
        click.secho(f"Error fetching account ID: {e}", fg="red")
        return None

def stack_exists():
    """Check if the CloudFormation stack exists."""
    try:
        cloudformation.describe_stacks(StackName=CLOUDFORMATION_STACK_NAME)
        return True
    except ClientError as e:
        if 'does not exist' in str(e):
            return False
        else:
            raise

def create_stack():
    """Create a CloudFormation stack."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    template_file = os.path.expanduser(os.path.join(script_dir, 'infra.yaml'))

    try:
        with open(template_file, 'r') as file:
            template_body = file.read()

        cloudformation.create_stack(
            StackName=CLOUDFORMATION_STACK_NAME,
            TemplateBody=template_body,
            Capabilities=['CAPABILITY_IAM', 'CAPABILITY_AUTO_EXPAND'],
            Tags=[{'Key': 'project', 'Value': 'oneoff'}],
        )
        click.echo(f"Stack creation initiated for {CLOUDFORMATION_STACK_NAME}.")
    except ClientError as e:
        click.secho(f"Failed to create stack: {e}", fg="red")
        raise

def wait_for_stack_creation():
    """Poll the status of the CloudFormation stack creation."""
    while True:
        try:
            response = cloudformation.describe_stacks(StackName=CLOUDFORMATION_STACK_NAME)
            status = response['Stacks'][0]['StackStatus']

            if status in ['CREATE_COMPLETE', 'ROLLBACK_COMPLETE', 'CREATE_FAILED']:
                break

            click.echo(f"Waiting for deployment to finish. Current status: {status}")
            time.sleep(5)
        except ClientError as e:
            click.secho(f"Failed to describe stack: {e}", fg="red")
            raise

def get_stack_outputs():
    """Retrieve specific outputs from the CloudFormation stack."""
    try:
        response = cloudformation.describe_stacks(StackName=CLOUDFORMATION_STACK_NAME)
        outputs = response['Stacks'][0].get('Outputs', [])
        return {output['OutputKey']: output['OutputValue'] for output in outputs}
    except ClientError as e:
        click.secho(f"Failed to retrieve stack outputs: {e}", fg="red")
        raise

def authenticate_docker_to_ecr(region, account_id):
    """Authenticate Docker to ECR."""
    ecr_client = boto3.client('ecr', region_name=region)
    
    try:
        response = ecr_client.get_authorization_token()
        auth_data = response['authorizationData'][0]
        auth_token = auth_data['authorizationToken']
        decoded_token = base64.b64decode(auth_token).decode('utf-8')
        username, password = decoded_token.split(':')
        registry = auth_data['proxyEndpoint']

        docker_login_command = f"docker login -u {username} -p {password} {registry}"
        subprocess.run(docker_login_command, shell=True, check=True)

        click.echo("Docker authenticated to ECR successfully!")
    except Exception as e:
        click.secho(f"Error authenticating Docker to ECR: {e}", fg="red")
        raise click.ClickException(f"Error authenticating Docker to ECR: {e}")

def build_and_tag_docker_image(dockerfile_path, image_name, tag):
    """Build and tag a Docker image."""
    try:
        click.echo("Building Docker image...")
        client = docker.from_env()
        client.images.build(path=os.path.expanduser(os.path.dirname(dockerfile_path)), tag=image_name, platform='linux/amd64')
        client.images.get(image_name).tag(image_name, tag)
        click.echo("Docker image built and tagged successfully!")
    except Exception as e:
        click.secho(f"Error building and tagging Docker image: {e}", fg="red")
        raise click.ClickException(f"Error building and tagging Docker image: {e}")

def push_docker_image_to_ecr(repo_name, image_name, tag, region, account_id):
    """Push the Docker image to ECR."""
    try:
        ecr_repository_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{repo_name}"
        full_image_name = f"{ecr_repository_uri}:{tag}"

        client = docker.from_env()
        client.images.get(f"{image_name}:{tag}").tag(ecr_repository_uri, tag)
        
        click.echo(f"Pushing image {full_image_name} to ECR...")
        client.images.push(ecr_repository_uri, tag=tag)
        click.echo("Docker image successfully pushed to ECR!")
    except Exception as e:
        click.secho(f"Error pushing Docker image to ECR: {e}", fg="red")
        raise click.ClickException(f"Error pushing Docker image to ECR: {e}")
    
def build_push(region, account_id, repo_name, image_name, tag, dockerfile_path):
    """Build, tag, and push a Docker image to ECR."""
    try:
        authenticate_docker_to_ecr(region, account_id)
        build_and_tag_docker_image(dockerfile_path, image_name, tag)
        push_docker_image_to_ecr(repo_name, image_name, tag, region, account_id)
    except click.ClickException as e:
        click.secho(f"Command failed: {e}", fg="red")
        sys.exit(1)

def get_or_create_cloudwatch_group(region, name):
    """Create a CloudWatch log group if it doesn't exist, or return the existing one."""
    logs_client = boto3.client('logs', region_name=region)
    log_group_name = f"/ecs/{name}"

    try:
        logs_client.create_log_group(logGroupName=log_group_name)
        click.echo(f"Log group '{log_group_name}' created successfully.")
        logs_client.put_retention_policy(logGroupName=log_group_name, retentionInDays=7)
    except logs_client.exceptions.ResourceAlreadyExistsException:
        click.echo(f"Log group '{log_group_name}' already exists.")

    return log_group_name

def create_ecs_task_definition(account_id, task_name, execution_role_arn, task_role_arn, container_name, ecr_repository_name, cpu, memory, log_group_name, region):
    """Create an ECS task definition."""
    ecs_client = boto3.client('ecs', region_name=region)
    
    try:
        response = ecs_client.register_task_definition(
            family=task_name,
            executionRoleArn=execution_role_arn,
            taskRoleArn=task_role_arn,
            networkMode='awsvpc',
            containerDefinitions=[
                {
                    'name': container_name,
                    'image': f"{account_id}.dkr.ecr.{region}.amazonaws.com/{ecr_repository_name}:latest",
                    'cpu': int(cpu),
                    'memory': int(memory),
                    'essential': True,
                    'logConfiguration': {
                        'logDriver': 'awslogs',
                        'options': {
                            'awslogs-group': log_group_name,
                            'awslogs-region': region,
                            'awslogs-stream-prefix': 'ecs'
                        }
                    }
                },
            ],
            requiresCompatibilities=['FARGATE'],
            cpu=str(cpu),
            memory=str(memory),
            tags=[{'key': 'project', 'value': 'oneoff'}],
        )
        return response['taskDefinition']['taskDefinitionArn']
    except Exception as e:
        click.secho(f"Could not create ECS task definition: {e}", fg="red")
        sys.exit(1)

def run_task(region, cluster_name, task_definition_name, subnet_id, security_group_id, name):
    """Run an ECS task using the Fargate launch type."""
    ecs_client = boto3.client('ecs', region_name=region)
    
    running_tasks = list_tasks_with_tag(cluster_name, region)
    for task in running_tasks:
        if task['name'] == name:
            click.echo('Job with the same name already running - stopping it')
            ecs_client.stop_task(
                cluster=cluster_name,
                task=task['taskArn'],
                reason=f"Stopping task to start a new one with the same name: {name}"
            )

    try:
        click.echo("Starting the container...")
        response = ecs_client.run_task(
            cluster=cluster_name,
            taskDefinition=task_definition_name,
            launchType='FARGATE',
            networkConfiguration={
                'awsvpcConfiguration': {
                    'subnets': [subnet_id],
                    'securityGroups': [security_group_id],
                    'assignPublicIp': 'ENABLED'
                }
            },
            tags=[
                {'key': 'project', 'value': 'oneoff'},
                {'key': 'name', 'value': name},
            ],
        )
        
        failures = response.get('failures')
        if failures:
            for failure in failures:
                click.secho(f"Failure: {failure.get('reason')} in {failure.get('arn')}", fg="red")
            raise click.ClickException("ECS task run failed.")
        
        click.echo(f"ECS task started successfully: {response['tasks'][0]['taskArn']}")
        task = response['tasks'][0]
        store_jobs([{'name': name, 'status': 'PENDING', 'created': task['createdAt'], 'taskArn': task['taskArn']}])

    except Exception as e:
        click.secho(f"An unexpected error occurred when running the task: {str(e)}", fg="red")
        sys.exit(1)

def time_ago(input_time):
    """Calculate and return a human-readable string representing the time elapsed since `input_time`."""
    now = datetime.now(timezone.utc)
    delta = now - input_time
    seconds = delta.total_seconds()

    if seconds < 60:
        return f"{int(seconds)} seconds ago"
    elif seconds < 3600:
        return f"{int(seconds // 60)} minutes ago"
    elif seconds < 86400:
        return f"{int(seconds // 3600)} hours ago"
    else:
        return f"{int(seconds // 86400)} days ago"
    
def list_tasks_with_tag(cluster_name, region):
    """
    s all ECS tasks in the given cluster that have a specific tag key-value pair.

    :param cluster_name: Name of the ECS cluster
    :param region: AWS region where the ECS cluster is located
    :return:  of tasks with specified tag
    """
    ecs_client = boto3.client('ecs', region_name=region)

    local_jobs = get_local_jobs()

    # Get the list of tasks in the cluster
    tasks_response = ecs_client.list_tasks(cluster=cluster_name)
    task_arns = tasks_response['taskArns']

    matching_tasks = []

    if not task_arns:
        # click.echo(f"No running tasks found in cluster {cluster_name}.")

        if not local_jobs:
            return []
        merged_tasks = merge_local_with_remote(local_jobs, [])

        return merged_tasks

    # Describe the tasks to get their tags
    task_descriptions = ecs_client.describe_tasks(cluster=cluster_name, tasks=task_arns)['tasks']
    
    for task in task_descriptions:
        tags = ecs_client.list_tags_for_resource(resourceArn=task['taskArn'])['tags']
        
        name_value = None
        project_value = None
        
        for tag in tags:
            if tag['key'] == 'name':
                name_value = tag['value']
            elif tag['key'] == 'project':
                project_value = tag['value']
        
        if project_value == 'oneoff' and name_value:
            matching_tasks.append({
                'name': name_value,
                'status': task['lastStatus'],
                'created': task['createdAt'],  # Ensure this is in datetime format
                'taskArn': task['taskArn']
            })

    local_jobs = get_local_jobs()
    merged_tasks = merge_local_with_remote(local_jobs, matching_tasks)
    store_jobs(merged_tasks)
    
    return merged_tasks


def get_latest_logs(log_group_name, log_stream_name=None, start_time=None, end_time=None, limit=10):
    """
    Fetches the latest logs from a specified CloudWatch Logs group.

    :param log_group_name: The name of the CloudWatch Logs group.
    :param log_stream_name: The name of the CloudWatch Logs stream (optional).
    :param start_time: The start time for the logs in milliseconds since epoch (optional).
    :param end_time: The end time for the logs in milliseconds since epoch (optional).
    :param limit: The maximum number of log events to retrieve.
    :return: A  of log events.
    """
    client = boto3.client('logs')

    # If start_time and end_time are not provided, fetch logs from the last hour
    if not start_time:
        start_time = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp() * 1000)
    if not end_time:
        end_time = int(datetime.now(timezone.utc).timestamp() * 1000)

    try:
        # Fetch the latest log stream if no log stream name is provided
        streams_response = client.describe_log_streams(
            logGroupName=log_group_name,
            orderBy='LastEventTime',
            descending=True,
            limit=1
        )

        if not streams_response['logStreams']:
            click.echo(f"No log streams found in log group: {log_group_name}")
            return []

        latest_log_stream = streams_response['logStreams'][0]['logStreamName']
        response = client.get_log_events(
            logGroupName=log_group_name,
            logStreamName=latest_log_stream,
            limit=limit,
            startFromHead=False  # To get the latest logs first
        )
        return response.get('events', [])

    except client.exceptions.ResourceNotFoundException as e:
        click.echo(f"Log group {log_group_name} or log stream {log_stream_name} not found: {str(e)}")
        return []
    except Exception as e:
        click.echo(f"An error occurred while fetching logs: {str(e)}")
        return []

def kill_job(name: str, region: str, cluster: str, task_arn: str) -> None:
    """
    Given a job name and task ARN, kill the associated ECS task.

    :param name: Name of the ECS task to stop.
    :param region: AWS region where the ECS cluster is located.
    :param cluster: ECS cluster name where the task is running.
    :param task_arn: The ARN of the ECS task to stop.
    """
    ecs_client = boto3.client('ecs', region_name=region)

    try:
        # Attempt to stop the ECS task
        ecs_client.stop_task(
            cluster=cluster,
            task=task_arn,
            reason=f"Manually stopping task '{name}' via oneoff CLI"
        )

        click.secho(f"Task '{name}' has seized to exist.", fg="white")
        
    except ClientError as e:
        # Handle specific errors from AWS SDK
        error_code = e.response['Error']['Code']
        if error_code == 'ResourceNotFoundException':
            click.secho(f"Error: Task with ARN '{task_arn}' does not exist.", fg="red")
        elif error_code == 'InvalidParameterException':
            click.secho(f"Error: Invalid parameters provided for task ARN '{task_arn}'.", fg="red")
        else:
            click.secho(f"Error: AWS SDK error occurred: {e}", fg="red")
    except Exception as e:
        # Handle any other unexpected errors
        click.secho(f"Error: An unexpected error occurred while trying to stop task '{task_arn}': {e}", fg="red")

def prune_jobs():
    """
    Delete all stopped ECS tasks from local state
    """
    try:
        os.remove(ONEOFF_CLI_JOBS_PATH)
        click.echo("All stopped jobs pruned successfully.")
    except FileNotFoundError:
        click.echo("No stopped jobs found.")