# OneOff

CLI that helps you quickly and easily run a long-running python program or any Docker container in a fargate task untill it completes.

## Prerequisites

- Active AWS credentials in your environment.

## Installation

In the root of the project, run

```bash
pip install .
```

to install the CLI

## Setup

Run

```bash
oneoff init
```

to invoke the one-time setup. This sets up infrastructure (that doesn't incur any cost) in your account to enable quickly run containers in ECS Fargate.

Run

```bash
oneoff --help
```

to see available commands.

## Overview

The CLI helps you run any python script or Docker container in as a task in ECS Fargate until the container or script completes and the task is terminated.

There are three types of invocations:

1. Run a python script without any dependencies
2. Run a python script with a custom `requirements.txt`file at the same level as the script.
3. Run an arbitrary container using a Dockerfile.

## Usage

Run a python script (with or without a `requirements.txt`):

```bash
oneoff run my_scripy.py --name test-run
```

Run an arbitrary container using a Dockerfile:

```bash
oneoff run path/to/Dockerfile --name test-run
```

> [!WARNING]  
> The IAM role created for the task contains admin permissions for simplicity.
