#!/bin/bash


ROOTDIR=$(dirname $BASH_SOURCE)
cd $ROOTDIR
source venv/bin/activate
pip install -e .
