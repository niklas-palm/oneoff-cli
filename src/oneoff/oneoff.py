import click
from oneoff.utils.cli import *
from oneoff.utils.aws import *
import json

class Config(object):
    def __init__(self) -> None:
        self.verbose = False
        self.update_from_conf_file()

    ALLOWED_KEYS = ["accountid", "region", "subnet", "securitygroup", "ecscluster", "ecrrepository", "taskexecutionrolearn", "taskrolearn", "ddbtablename"]

    # Merge existing conf with Config object
    def update_from_conf_file(self):
        conf = get_configuration()
        if conf:
            for key, value in conf.items():
                if key in self.ALLOWED_KEYS:
                    setattr(self, key, value)


pass_config = click.make_pass_decorator(Config, ensure=True)

@click.group()
@click.option("-v", "--verbose", is_flag=True)
@pass_config
def cli(config, verbose):
    config.verbose = verbose


@cli.command()
def init():
    """Initializes the CLI and deploys the relevant infra"""
    region = click.prompt(
        "What AWS region do you want to use?", type=str, default="eu-west-1"
    )

    # Deply cloudformation stack with ECS infra
    if stack_exists():
        click.echo(f"\nCloudformation stack already exists - using existing stack.")
    else:
        click.echo("\nDeploying necessary infrastructure. This may take a minute")
        create_stack()
        wait_for_stack_creation()
        click.secho(
            "Infrastructure deployed!",
            fg="green",
        )

    click.echo(f"Fetching stack outputs...")

    # Retrieve the outputs after stack creation or if it already exists
    outputs = get_stack_outputs()
    accountid = get_current_account_id()

    keys_of_interest = [
        'Subnet',
        'SecurityGroup',
        'ECSCluster',
        'ECRRepository',
        'TaskExecutionRoleArn',
        'TaskRoleArn',
        'DdbTableName'
    ]

    config = {}

    for key in keys_of_interest:
        if key in outputs:
            click.echo(f"{key}: {outputs[key]}")
            config[key.lower()] = outputs[key]
        else:
            click.echo(f"{key}: Not found in outputs")
            raise

    config = {"region": region, "accountid": accountid, **config}
    store_configuration(config)

    click.secho("\n oneoff will use the the following configuration: ", fg="cyan")
    formatted_config = json.dumps(config, indent=4, sort_keys=True)
    click.secho(formatted_config, fg="cyan")


    # click.secho("\n\U0001F973 oneoff cli is now ready to be usedn", fg="green", bold=True)

    click.echo("\n")
    click.echo("╔═══════════════════════════════════════════╗")
    click.echo("║  " + click.style("\U0001F973 oneoff cli is now ready to be used", fg="green", bold=True) + "    ║")
    click.echo("║                                           ║")
    click.echo("║ Get started:                              ║")
    click.echo("║                                           ║")
    click.echo("║ ** Run any python script in a default     ║")
    click.echo("║    container on Fargate:                  ║")
    click.echo("║    " + click.style("oneoeff run myscript.py -n job-name", fg="cyan", bold=True) + "    ║")
    click.echo("║                                           ║")
    click.echo("║ ** Run a container from a Dockerfile      ║")
    click.echo("║    in the current directory on Fargate:   ║")
    click.echo("║    " + click.style("oneoeff run . -n job-name", fg="cyan", bold=True) + "              ║")
    click.echo("║                                           ║")
    click.echo("║ ** List jobs:                             ║")
    click.echo("║    " + click.style("oneoeff jobs", fg="cyan", bold=True) + "                           ║")
    click.echo("║                                           ║")
    click.echo("║ ** Get the logs from a job:               ║")
    click.echo("║    " + click.style("oneoeff logs -n job-name", fg="cyan", bold=True) + "               ║")
    click.echo("║                                           ║")
    click.echo("║ ** See available commands:                ║")
    click.echo("║    " + click.style("oneoff --help", fg="cyan", bold=True) + "                          ║")
    click.echo("║                                           ║")
    click.echo("╚═══════════════════════════════════════════╝")

@cli.command()
@pass_config
@require_cli_config
def get_conf(config):
    """Prints current configuration"""
    click.secho(json.dumps(get_configuration(), indent=3), fg="cyan")

# ------ TODO: 


@cli.command()
@pass_config
@click.argument("script", required=True)
@click.option("-n", "--name", required=True, help="Name of the oneoff job")
@click.option("-m", "--memory", default=1024, help="Amount of memory in MB. Default = 1024")
@click.option("-c", "--cpu", default=512, help="Amount of cpu units. Default = 512")
@click.option("-s", "--storage", default=20, help="Amount of GB ephemeral storage [20, 200]. Default = 20")
@require_cli_config
def run(config, script, name, memory, cpu, storage):
    """Builds and runs the Dockerfile in the current directory"""
    
    # Verify Docker is running
    if not docker_is_running():
        click.secho("Docker is not running. Please start Docker and try again.", fg="red")
        return

    # Dockerfile sould be provided - build and run!
    if os.path.isdir(os.path.expanduser(script)) or script == 'Dockerfile':
        dockerfile_path = get_absolute_path_if_exists('Dockerfile')
        if not dockerfile_path:
            cwd = get_current_directory()
            click.secho(f"\nCould not find a Dockerfile in {cwd}/", fg="red")
            return
        
        build_push(config.region, config.accountid, config.ecrrepository, name, 'latest', dockerfile_path)


    # script provided - build and run!
    elif '.py' in script:
        script_path = get_absolute_path_if_exists(script)
        if not script_path:
            cwd = get_current_directory()
            click.secho(f"\nCould not find script {cwd}/{script}", fg="red")
            return

        click.echo(f"Running {script} in a default container...")

        requirements_path = get_absolute_path_if_exists('requirements.txt')
        if not requirements_path:
            click.echo(f"(Add a 'requirements.txt' in the same directory as your script if you need any additional packages installed)")
        else:
            click.echo(f"Using requirements from {requirements_path}")

        temp_dockerfile_path = create_temp_dockerfile(requirements_path, script_path, script)
        build_push(config.region, config.accountid, config.ecrrepository, name, 'latest', temp_dockerfile_path)


        log_group_name = get_or_create_cloudwatch_group(config.region, name)
        task_definition_arn = create_ecs_task_definition(config.accountid, name, config.taskexecutionrolearn, config.taskrolearn, name, config.ecrrepository, cpu, memory, log_group_name, config.region)
        run_task(config.region, config.ecscluster, task_definition_arn, config.subnet, config.securitygroup)

    else:
        cwd = get_current_directory()
        click.secho(f"\n{cwd}/{script} is neither a Dockerfile or valid script", fg="red")







@cli.command()
@pass_config
@click.option("-n", "--name", help="Name of the oneoff job")
@require_cli_config
def logs(config, name):
    """Fetches the logs for the oneoff job with the specifed name"""
    click.secho(json.dumps(get_configuration(), indent=3), fg="cyan")

@cli.command()
@pass_config
@require_cli_config
def list(config):
    """Lists oneoff jobs"""
    # print(get_absolute_paths('Dockerfile', 'requirements.txt'))
    dockerfile_path = get_absolute_path_if_exists('Dockerfile')
    requirements_path = get_absolute_path_if_exists('requirements.txt')
    # click.secho(json.dumps(get_configuration(), indent=3), fg="cyan")


@cli.command()
@pass_config
@require_cli_config
def test(config):
    """Lists oneoff jobs"""
