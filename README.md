# OneOff

CLI that helps you quickly and easily run a long-running python program or any Docker container in a fargate task untill it completes.

## Prerequisites

- Active AWS credentials in your environment.

## Installation

To install the CLI tool, navigate to the root of the project directory and run:

```bash
pip install .
```

## Setup

Initialize the CLI tool by running:

```bash
oneoff init
```

This command will set up the necessary AWS infrastructure (which incurs no cost) to enable you to quickly run containers in ECS Fargate.

After initialization, you can view available commands by running:

```bash
oneoff --help
```

to see available commands.

## Overview

The CLI helps you run any python script or Docker container in a task in ECS Fargate until the container or script completes and the task is terminated.

There are three types of invocations:

1. **Run a Python script without dependencies**: Executes a Python script using a default container.
2. **Run a Python script with a custom `requirements.txt` file**: Uses a `requirements.txt` file located at the same level as the script to install additional dependencies.
3. **Run an arbitrary container from a Dockerfile**: Builds and runs a Docker container using a Dockerfile located in the specified path.

## Usage

### Running a Python Script

To run a Python script (with or without a `requirements.txt`):

```bash
oneoff run my_scripy.py --name test-run
```

### Running a Docker Container

To run a Docker container from a Dockerfile located in the current directory:

```bash
oneoff run . --name test-run
```

### Listing Running Jobs

To list all running oneoff jobs:

```bash
oneoff ls
```

This command displays the job name, status, and creation time of each running job.

### Fetching Logs

To fetch the logs for a specific job:

```bash
oneoff logs -n job-name
```

To continuously tail the logs (real-time updates) for a job:

```bash
oneoff logs -n job-name -t
```

> [!NOTE]  
> Logs are automatically deleted after 7 days.

### Killing a running job

To kill a running job:

```bash
oneoff kill name_of_job
```

### Clean up old state

To remove old jobs from the `ls` output and clean up metadata:

```bash
oneoff prune
```

### Viewing Current Configuration

To view the current configuration:

```bash
oneoff get_conf
```

## Notes

### CPU and Memory.

When specifying CPU and memory, the combination of values supported can be found in the [AWS docs here](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task_definition_parameters.html)

> [!WARNING]  
> The IAM role created for the task contains admin permissions for simplicity.
