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

cloudformation = boto3.client('cloudformation')
cloudformation_stack_name = 'oneoff-cli'

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
        cloudformation.describe_stacks(StackName=cloudformation_stack_name)
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
            StackName=cloudformation_stack_name,
            TemplateBody=template_body,
            Capabilities=['CAPABILITY_IAM', 'CAPABILITY_AUTO_EXPAND'],
            Tags=[{'Key': 'project', 'Value': 'oneoff'}],
        )
        click.echo(f"Stack creation initiated for {cloudformation_stack_name}.")
    except ClientError as e:
        click.secho(f"Failed to create stack: {e}", fg="red")
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
            click.secho(f"Failed to describe stack: {e}", fg="red")
            raise

def get_stack_outputs():
    """Retrieve specific outputs from the CloudFormation stack."""
    try:
        response = cloudformation.describe_stacks(StackName=cloudformation_stack_name)
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
        if task['job_name'] == name:
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
    Lists all ECS tasks in the given cluster that have a specific tag key-value pair.

    :param cluster_name: Name of the ECS cluster
    :param region: AWS region where the ECS cluster is located
    :return: List of tasks with specified tag
    """
    ecs_client = boto3.client('ecs', region_name=region)

    # Get the list of tasks in the cluster
    tasks_response = ecs_client.list_tasks(cluster=cluster_name)
    task_arns = tasks_response['taskArns']

    matching_tasks = []

    if not task_arns:
        click.echo(f"No running tasks found in cluster {cluster_name}.")
        return matching_tasks

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
                'job_name': name_value,
                'status': task['lastStatus'],
                'created': time_ago(task['createdAt']),
                'taskArn': task['taskArn']
            })
    
    return matching_tasks

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
