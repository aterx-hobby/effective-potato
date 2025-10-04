# effective-potato!

This projects goal is to create an MCP server that hosts a sandboxed ubuntu:24.04 image

This docker ubuntu 24.04 image should include the packages:
    build-essential, the rustup snap package, golang-1.23, xorg-server-fbdev, python3, python3-pip, python3-venv

The container will run "sleep infinity" as it's docker start up task so that the container runs infinitely
On start up of the application we'll rebuild the container and then start it
We need to maintain a local "workspace" directory that is mounted read/write to the container so that we don't need
to constantly copy files in and out.

Once we have the container build out working the next functional item to work on will be to run specific commands.

Instead of attempting to run them via "docker exec <containerid> -- /path/to/bin lots of arguments that could get messed up"

We should write all the commands needed to be executed into a script file in the mounted workspace directory.

lets use the following path:   workspace/.tmp_agent_scripts/

Example: we want to run the command "ls -ltrah /"

We'd create bash script

```
#!/bin/bash

ls -ltrah
```

copy it to workspace/.tmp_agent_scripts/task_<taskid>.sh mark it executable and then run

docker exec <containerid> -- /path/to/workspace/.tmp_agent_scripts/task_<taskid>.sh


