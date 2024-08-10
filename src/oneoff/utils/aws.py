import boto3
from botocore.exceptions import ClientError
import time
import click
import os
import docker
import subprocess
import  base64
import sys
import json
from datetime import datetime, timezone, timedelta

cloudformation = boto3.client('cloudformation')

cloudformation_stack_name = 'oneoff-cli'

def get_current_account_id():
    """Fetch the current AWS account ID using boto3."""
    try:
        # Create an STS client
        sts_client = boto3.client('sts')
        
        # Get the current caller identity
        response = sts_client.get_caller_identity()
        
        # Extract the account ID from the response
        account_id = response['Account']
        
        return account_id

    except Exception as e:
        print(f"Error fetching account ID: {e}")
        return None

def stack_exists():
    """Check if the CloudFormation stack exists."""
    try:
        response = cloudformation.describe_stacks(StackName=cloudformation_stack_name)
        return True
    except ClientError as e:
        if 'does not exist' in str(e):
            return False
        else:
            raise e

def create_stack():
    """Create a CloudFormation stack."""
    # Get the directory of the current script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Construct the absolute path to the template file relative to the script's directory

    template_file = os.path.expanduser(os.path.join(script_dir, 'infra.yaml'))
    with open(template_file, 'r') as file:
        template_body = file.read()

    try:
        response = cloudformation.create_stack(
            StackName=cloudformation_stack_name,
            TemplateBody=template_body,
            Capabilities=['CAPABILITY_IAM', 'CAPABILITY_AUTO_EXPAND'],
            tags=[
                {
                    'key': 'project',
                    'value': 'oneoff'
                },
            ],
        )
        return
    except ClientError as e:
        click.secho(
            f"\nFailed to create stack: {e}",
            fg="red",
        )
        raise

def wait_for_stack_creation():
    """Poll the status of the CloudFormation stack creation."""
    while True:
        try:
            response = cloudformation.describe_stacks(StackName=cloudformation_stack_name)
            status = response['Stacks'][0]['StackStatus']

            if status in ['CREATE_COMPLETE', 'ROLLBACK_COMPLETE', 'CREATE_FAILED']:
                break
            click.echo(f"Waiting for deployment to finish. Current status: {status}")
            time.sleep(5)
        except ClientError as e:
            click.secho(
                f"\nFailed to describe stack: {e}",
                fg="red",
            )
            raise

def get_stack_outputs():
    """Retrieve specific outputs from the CloudFormation stack."""
    try:
        response = cloudformation.describe_stacks(StackName=cloudformation_stack_name)
        outputs = response['Stacks'][0].get('Outputs', [])

        output_dict = {output['OutputKey']: output['OutputValue'] for output in outputs}
        return output_dict
    except ClientError as e:
        click.secho(
            f"\nFailed to retrieve stack outputs: {e}",
            fg="red",
        )
        raise


def authenticate_docker_to_ecr(region, account_id):
    """Authenticate Docker to ECR."""
    ecr_client = boto3.client('ecr', region_name=region)
    
    try:
        # Get the authorization token from ECR
        response = ecr_client.get_authorization_token()
        auth_data = response['authorizationData'][0]
        
        # Decode the authorization token
        auth_token = auth_data['authorizationToken']
        decoded_token = base64.b64decode(auth_token).decode('utf-8')
        username, password = decoded_token.split(':')
        
        # Get the registry endpoint
        registry = auth_data['proxyEndpoint']
        
        # Authenticate Docker to ECR
        docker_login_command = f"docker login -u {username} -p {password} {registry}"
        subprocess.run(docker_login_command, shell=True, check=True)
        
        click.echo("Docker authenticated to ECR successfully!")
    
    except Exception as e:
        click.echo(f"Error authenticating Docker to ECR: {e}", err=True)
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
        click.echo(f"Error building and tagging Docker image: {e}", err=True)
        raise click.ClickException(f"Error building and tagging Docker image: {e}")

def push_docker_image_to_ecr(repo_name, image_name, tag, region, account_id):
    """Push the Docker image to ECR."""
    try:
        ecr_repository_uri = f"{account_id}.dkr.ecr.{region}.amazonaws.com/{repo_name}"
        full_image_name = f"{ecr_repository_uri}:{tag}"
        
        client = docker.from_env()
        client.images.get(f"{image_name}:{tag}").tag(ecr_repository_uri, tag)
        
        # Push the image to ECR
        click.echo(f"Pushing image {full_image_name} to ECR...")
        
        client.images.push(ecr_repository_uri, tag=tag)

        # VERBOSE
        # # Push the image to ECR
        # push_logs = client.images.push(ecr_repository_uri, tag=tag, stream=True)

        # # Print push logs
        # for line in push_logs:
        #     click.secho(line.decode('utf-8').strip(), nl=True, fg='yellow')
        
        click.echo("Docker image successfully pushed to ECR!")
    except Exception as e:
        click.echo(f"Error pushing Docker image to ECR: {e}", err=True)
        raise click.ClickException(f"Error pushing Docker image to ECR: {e}")
    
def build_push(region, account_id, repo_name, image_name, tag, dockerfile_path):
    """Build, tag, and push a Docker image to ECR."""
    try:
        # Build and push image to ECR
        authenticate_docker_to_ecr(region, account_id)
        build_and_tag_docker_image(dockerfile_path, image_name, tag)
        push_docker_image_to_ecr(repo_name, image_name, tag, region, account_id)

    except click.ClickException as e:
        click.echo(f"Command failed: {e}", err=True)
        exit(1)

def get_or_create_cloudwatch_group(region, name):
    """Create a CloudWatch log group if it doesn't exist, or return the existing one."""
    logs_client = boto3.client('logs', region_name=region)
    log_group_name = f"/ecs/{name}"

    try:
        # Try to create the log group
        logs_client.create_log_group(logGroupName=log_group_name)
        click.echo(f"Log group '{log_group_name}' created successfully.")
        logs_client.put_retention_policy(
            logGroupName=log_group_name,
            retentionInDays=7
        )
    except logs_client.exceptions.ResourceAlreadyExistsException:
        click.echo(f"Log group '{log_group_name}' already exists.")

    return log_group_name

def create_ecs_task_definition(account_id, task_name, execution_role_arn, task_role_arn, container_name, ecr_repository_name, cpu, memory, log_group_name, region):
    """Create an ECS task definition."""
    ecs_client = boto3.client('ecs', region_name=region)
    
    try:

        # Register the ECS task definition
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
            tags=[
                {
                    'key': 'project',
                    'value': 'oneoff'
                },
            ],
        )

        return response['taskDefinition']['taskDefinitionArn']

    except click.ClickException as e:
        click.echo(f"Could not create ECS task definition: {e}", err=True)
        exit(1)

def run_task(region, cluster_name, task_definition_name, subnet_id, security_group_id, name):
    """Run an ECS task using the Fargate launch type."""
    ecs_client = boto3.client('ecs', region_name=region)

    click.echo(f"Starting the container")

    
    try:
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
                {
                    'key': 'project',
                    'value': 'oneoff'
                },
                {
                    'key': 'name',
                    'value': name
                },
            ],
        )
        
        # Check for failures
        failures = response.get('failures')
        if failures:
            for failure in failures:
                click.echo(f"Failure: {failure.get('reason')} in {failure.get('arn')}", err=True)
            raise click.ClickException("ECS task run failed.")
        
        click.echo(f"ECS task started successfully: {response['tasks'][0]['taskArn']}")
    
    except Exception as e:
        click.echo(f"An unexpected error occurred when running the task: {str(e)}", err=True)
        sys.exit(1)


def time_ago(input_time):
    # Get the current time in UTC
    now = datetime.now(timezone.utc)
    
    # Calculate the time difference between now and the input time
    delta = now - input_time
    
    # Convert the difference to seconds
    seconds = delta.total_seconds()

    # Determine the appropriate unit of time to return
    if seconds < 60:
        return f"{int(seconds)} seconds ago"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{int(minutes)} minutes ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{int(hours)} hours ago"
    elif seconds < 2592000:
        days = seconds // 86400
        return f"{int(days)} days ago"
    elif seconds < 31536000:
        months = seconds // 2592000
        return f"{int(months)} months ago"
    else:
        years = seconds // 31536000
        return f"{int(years)} years ago"


def list_tasks_with_tag(cluster_name, region):
    """
    Lists all ECS tasks in the given cluster that have a specific tag key-value pair.

    :param cluster_name: Name of the ECS cluster
    :param region_name: AWS region where the ECS cluster is located
    :param tag_key: Tag key to filter tasks by (default is 'project')
    :param tag_value: Tag value to filter tasks by (default is 'oneoff')
    :return: List of task ARNs with the specified tag
    """
    ecs_client = boto3.client('ecs', region_name=region)

    # Get list of tasks in the cluster
    tasks = ecs_client.list_tasks(cluster=cluster_name)
    task_arns = tasks['taskArns']

    matching_tasks = []

    if not task_arns:
        print(f"No running tasks found in cluster {cluster_name}.")
        return matching_tasks

    tasks = []
    # Describe the tasks to get the tags
    task_descriptions = ecs_client.describe_tasks(cluster=cluster_name, tasks=task_arns)['tasks']
    for task in task_descriptions:
        print(task)
        tags = ecs_client.list_tags_for_resource(resourceArn=task['taskArn'])
        name_value = None
        project_value = None

        for tag in tags['tags']:
            if tag['key'] == 'name':
                name_value = tag['value']
            elif tag['key'] == 'project':
                project_value = tag['value']
        if project_value == 'oneoff' and name_value:
            tasks.append({'job_name': name_value, 'status': task['lastStatus'], 'version': task['version'], 'created': time_ago(task['createdAt'])})

    return tasks

def get_latest_logs(log_group_name, log_stream_name=None, start_time=None, end_time=None, limit=10):
    """
    Fetches the latest logs from a specified CloudWatch Logs group.
    
    :param log_group_name: The name of the CloudWatch Logs group.
    :param log_stream_name: The name of the CloudWatch Logs stream (optional).
    :param start_time: The start time for the logs in milliseconds since epoch (optional).
    :param end_time: The end time for the logs in milliseconds since epoch (optional).
    :param limit: The maximum number of log events to retrieve.
    :return: A list of log events.
    """
    # Create a CloudWatch Logs client
    client = boto3.client('logs')
    
    # If start_time and end_time are not provided, fetch logs from the last 1 hour
    if not start_time:
        start_time = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp() * 1000)
    if not end_time:
        end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
    
    try:
        # Fetch the latest log streams if no log stream name is provided
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
            # startTime=start_time,
            # endTime=end_time,
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

# Example usage within a Click command
@click.command()
@click.option('--log-group-name', prompt='Log Group Name', help='The name of the CloudWatch Logs group.')
@click.option('--log-stream-name', help='The name of the CloudWatch Logs stream (optional).')
@click.option('--limit', default=10, help='The maximum number of log events to retrieve.')
def fetch_logs(log_group_name, log_stream_name, limit):
    logs = get_latest_logs(log_group_name, log_stream_name, limit=limit)
    for log in logs:
        timestamp = datetime.fromtimestamp(log['timestamp'] / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        click.echo(f"{timestamp} - {log['message'].strip()}")

if __name__ == '__main__':
    fetch_logs()
