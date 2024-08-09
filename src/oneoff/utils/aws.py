import boto3
from botocore.exceptions import ClientError
import time
import click
import os
import docker
import subprocess
import  base64
import sys

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

def run_task(region, cluster_name, task_definition_name, subnet_id, security_group_id):
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
            }
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
