#
#    ICRAR - International Centre for Radio Astronomy Research
#    (c) UWA - The University of Western Australia
#    Copyright by UWA (in the framework of the ICRAR)
#    All rights reserved
#
#    This library is free software; you can redistribute it and/or
#    modify it under the terms of the GNU Lesser General Public
#    License as published by the Free Software Foundation; either
#    version 2.1 of the License, or (at your option) any later version.
#
#    This library is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#    Lesser General Public License for more details.
#
#    You should have received a copy of the GNU Lesser General Public
#    License along with this library; if not, write to the Free Software
#    Foundation, Inc., 59 Temple Place, Suite 330, Boston,
#    MA 02111-1307  USA
#
"""
Build a dictionary for the execution graph
"""
import argparse
import getpass
import httplib
import json
import logging
import os
from time import sleep

import boto3
from configobj import ConfigObj

from aws_chiles02.build_graph_uvsub import BuildGraphUvsub
from aws_chiles02.common import get_session_id, get_argument, get_aws_credentials, get_uuid
from aws_chiles02.ec2_controller import EC2Controller
from aws_chiles02.generate_common import get_reported_running, build_hosts, get_nodes_running
from aws_chiles02.settings_file import AWS_REGION, AWS_AMI_ID, DIM_PORT
from aws_chiles02.user_data import get_node_manager_user_data, get_data_island_manager_user_data
from dfms.manager.client import DataIslandManagerClient

LOG = logging.getLogger(__name__)
PARALLEL_STREAMS = 8


class WorkToDo:
    def __init__(self, width, bucket_name, s3_uvsub_name, s3_split_name):
        self._width = width
        self._bucket_name = bucket_name
        self._s3_uvsub_name = s3_uvsub_name
        self._s3_split_name = s3_split_name
        self._work_already_done = None
        self._bucket = None
        self._list_frequencies = None
        self._work_to_do = []

    def calculate_work_to_do(self):
        session = boto3.Session(profile_name='aws-chiles02')
        s3 = session.resource('s3', use_ssl=False)

        uvsub_objects = []
        self._bucket = s3.Bucket(self._bucket_name)
        for key in self._bucket.objects.filter(Prefix='{0}'.format(self._s3_uvsub_name)):
            uvsub_objects.append(key.key)
            LOG.info('uvsub {0} found'.format(key.key))

        for key in self._bucket.objects.filter(Prefix='{0}'.format(self._s3_split_name)):
            LOG.info('split {0} found'.format(key.key))
            elements = key.key.split('/')
            if len(elements) == 3:
                expected_uvsub_name = 'uvsub_{0}/{1}/{2}'.format(
                    self._width,
                    elements[1],
                    elements[2],
                )
                if expected_uvsub_name not in uvsub_objects:
                    self._work_to_do.append(
                        [
                            elements[1],
                            elements[2],
                        ]
                    )

    @property
    def work_to_do(self):
        return self._work_to_do


def get_s3_split_name(width):
    return 'split_{0}'.format(width)


def get_s3_uvsub_name(width):
    return 'uvsub_{0}'.format(width)


def get_nodes_required(node_count, spot_price):
    nodes = [{
        'number_instances': node_count,
        'instance_type': 'i2.2xlarge',
        'spot_price': spot_price
    }]

    return nodes, node_count


def create_and_generate(bucket_name, frequency_width, ami_id, spot_price, volume, nodes, add_shutdown):
    boto_data = get_aws_credentials('aws-chiles02')
    if boto_data is not None:
        work_to_do = WorkToDo(frequency_width, bucket_name, get_s3_uvsub_name(frequency_width), get_s3_split_name(frequency_width))
        work_to_do.calculate_work_to_do()

        nodes_required, node_count = get_nodes_required(nodes, spot_price)

        if len(nodes_required) > 0:
            uuid = get_uuid()
            ec2_data = EC2Controller(
                ami_id,
                nodes_required,
                get_node_manager_user_data(boto_data, uuid, max_request_size=50),
                AWS_REGION,
                tags=[
                    {
                        'Key': 'Owner',
                        'Value': getpass.getuser(),
                    },
                    {
                        'Key': 'Name',
                        'Value': 'DFMS Node - Uvsub',
                    },
                    {
                        'Key': 'uuid',
                        'Value': uuid,
                    }
                ]
            )
            ec2_data.start_instances()

            reported_running = get_reported_running(
                uuid,
                node_count,
                wait=600
            )

            if len(reported_running) == 0:
                LOG.error('Nothing has reported ready')
            else:
                hosts = build_hosts(reported_running)

                # Create the Data Island Manager
                data_island_manager = EC2Controller(
                    ami_id,
                    [
                        {
                            'number_instances': 1,
                            'instance_type': 'm4.large',
                            'spot_price': spot_price
                        }
                    ],
                    get_data_island_manager_user_data(boto_data, hosts, uuid, max_request_size=50),
                    AWS_REGION,
                    tags=[
                        {
                            'Key': 'Owner',
                            'Value': getpass.getuser(),
                        },
                        {
                            'Key': 'Name',
                            'Value': 'Data Island Manager - Uvsub',
                        },
                        {
                            'Key': 'uuid',
                            'Value': uuid,
                        }
                    ]
                )
                data_island_manager.start_instances()
                data_island_manager_running = get_reported_running(
                        uuid,
                        1,
                        wait=600
                )

                if len(data_island_manager_running['m4.large']) == 1:
                    # Now build the graph
                    session_id = get_session_id()
                    graph = BuildGraphUvsub(work_to_do.work_to_do, bucket_name, volume, PARALLEL_STREAMS, reported_running, add_shutdown, frequency_width, session_id)
                    graph.build_graph()

                    instance_details = data_island_manager_running['m4.large'][0]
                    host = instance_details['ip_address']
                    LOG.info('Connection to {0}:{1}'.format(host, DIM_PORT))
                    client = DataIslandManagerClient(host, DIM_PORT)

                    client.create_session(session_id)
                    client.append_graph(session_id, graph.drop_list)
                    client.deploy_session(session_id, graph.start_oids)
    else:
        LOG.error('Unable to find the AWS credentials')


def use_and_generate(host, port, bucket_name, frequency_width, volume, add_shutdown):
    boto_data = get_aws_credentials('aws-chiles02')
    if boto_data is not None:
        connection = httplib.HTTPConnection(host, port)
        connection.request('GET', '/api', None, {})
        response = connection.getresponse()
        if response.status != httplib.OK:
            msg = 'Error while processing GET request for {0}:{1}/api (status {2}): {3}'.format(host, port, response.status, response.read())
            raise Exception(msg)

        json_data = response.read()
        message_details = json.loads(json_data)
        host_list = message_details['hosts']

        nodes_running = get_nodes_running(host_list)
        if len(nodes_running) > 0:
            work_to_do = WorkToDo(frequency_width, bucket_name, get_s3_uvsub_name(frequency_width), get_s3_split_name(frequency_width))
            work_to_do.calculate_work_to_do()

            # Now build the graph
            session_id = get_session_id()
            graph = BuildGraphUvsub(work_to_do.work_to_do, bucket_name, volume, PARALLEL_STREAMS, nodes_running, add_shutdown, frequency_width, session_id)
            graph.build_graph()

            LOG.info('Connection to {0}:{1}'.format(host, port))
            client = DataIslandManagerClient(host, port)

            client.create_session(session_id)
            client.append_graph(session_id, graph.drop_list)
            client.deploy_session(session_id, graph.start_oids)

        else:
            LOG.warning('No nodes are running')


def generate_json(width, bucket, nodes, volume, shutdown):
    work_to_do = WorkToDo(width, bucket, get_s3_uvsub_name(width), get_s3_split_name(width))
    work_to_do.calculate_work_to_do()

    node_details = {
        'i2.2xlarge': [{'ip_address': 'node_i2_{0}'.format(i)} for i in range(0, nodes)],
    }
    graph = BuildGraphUvsub(work_to_do.work_to_do, bucket, volume, PARALLEL_STREAMS, node_details, shutdown, width, 'session_id')
    graph.build_graph()
    json_dumps = json.dumps(graph.drop_list, indent=2)
    LOG.info(json_dumps)
    with open("/tmp/json_uvsub.txt", "w") as json_file:
        json_file.write(json_dumps)


def command_json(args):
    generate_json(args.width, args.bucket, args.nodes, args.volume, args.shutdown)


def command_create(args):
    create_and_generate(
        args.bucket,
        args.width,
        args.ami,
        args.spot_price1,
        args.volume,
        args.nodes,
        args.shutdown,
    )


def command_use(args):
    use_and_generate(
        args.host,
        args.port,
        args.bucket,
        args.width,
        args.volume,
        args.shutdown,
    )


def command_interactive(args):
    LOG.info(args)
    sleep(0.5)  # Allow the logging time to print
    path_dirname, filename = os.path.split(__file__)
    root, ext = os.path.splitext(filename)
    config_file_name = '{0}/{1}.settings'.format(path_dirname, root)
    if os.path.exists(config_file_name):
        config = ConfigObj(config_file_name)
    else:
        config = ConfigObj()
        config.filename = config_file_name

    get_argument(config, 'run_type', 'Create, use or json', allowed=['create', 'use', 'json'], help_text=' use a network or create a network or just produce the JSON')
    if config['run_type'] == 'create':
        get_argument(config, 'ami', 'AMI Id', help_text='the AMI to use', default=AWS_AMI_ID)
        get_argument(config, 'spot_price', 'Spot Price for i2.2xlarge', help_text='the spot price')
        get_argument(config, 'bucket_name', 'Bucket name', help_text='the bucket to access', default='13b-266')
        get_argument(config, 'volume', 'Volume', help_text='the directory on the host to bind to the Docker Apps')
        get_argument(config, 'width', 'Frequency width', data_type=int, help_text='the frequency width', default=4)
        get_argument(config, 'nodes', 'Number of nodes', data_type=int, help_text='the number of nodes', default=1)
        get_argument(config, 'shutdown', 'Add the shutdown node', data_type=bool, help_text='add a shutdown drop', default=True)
    elif config['run_type'] == 'use':
        get_argument(config, 'dim', 'Data Island Manager', help_text='the IP to the DataIsland Manager')
        get_argument(config, 'bucket_name', 'Bucket name', help_text='the bucket to access', default='13b-266')
        get_argument(config, 'volume', 'Volume', help_text='the directory on the host to bind to the Docker Apps')
        get_argument(config, 'width', 'Frequency width', data_type=int, help_text='the frequency width', default=4)
        get_argument(config, 'shutdown', 'Add the shutdown node', data_type=bool, help_text='add a shutdown drop', default=True)
    else:
        get_argument(config, 'bucket_name', 'Bucket name', help_text='the bucket to access', default='13b-266')
        get_argument(config, 'volume', 'Volume', help_text='the directory on the host to bind to the Docker Apps')
        get_argument(config, 'width', 'Frequency width', data_type=int, help_text='the frequency width', default=4)
        get_argument(config, 'nodes', 'Number of nodes', data_type=int, help_text='the number of nodes', default=1)
        get_argument(config, 'shutdown', 'Add the shutdown node', data_type=bool, help_text='add a shutdown drop', default=True)

    # Write the arguments
    config.write()

    # Run the command
    if config['run_type'] == 'create':
        create_and_generate(
            config['bucket_name'],
            config['width'],
            config['ami'],
            config['spot_price'],
            config['volume'],
            config['nodes'],
            config['shutdown'],
        )
    elif config['run_type'] == 'use':
        use_and_generate(
            config['dim'],
            DIM_PORT,
            config['bucket_name'],
            config['width'],
            config['volume'],
            config['shutdown'],
        )
    else:
        generate_json(
            config['width'],
            config['bucket_name'],
            config['nodes'],
            config['volume'],
            config['shutdown'],
        )


def parser_arguments():
    parser = argparse.ArgumentParser('Build the UVSUB physical graph for a day')

    subparsers = parser.add_subparsers()

    parser_json = subparsers.add_parser('json', help='display the json')
    parser_json.add_argument('bucket', help='the bucket to access')
    parser_json.add_argument('volume', help='the directory on the host to bind to the Docker Apps')
    parser_json.add_argument('parallel_streams', type=int, help='the of parallel streams')
    parser_json.add_argument('-w', '--width', type=int, help='the frequency width', default=4)
    parser_json.add_argument('-n', '--nodes', type=int, help='the number of nodes', default=1)
    parser_json.add_argument('-s', '--shutdown', action="store_true", help='add a shutdown drop')
    parser_json.set_defaults(func=command_json)

    parser_create = subparsers.add_parser('create', help='run and deploy')
    parser_create.add_argument('ami', help='the ami to use')
    parser_create.add_argument('spot_price', type=float, help='the spot price')
    parser_create.add_argument('bucket', help='the bucket to access')
    parser_create.add_argument('volume', help='the directory on the host to bind to the Docker Apps')
    parser_create.add_argument('-w', '--width', type=int, help='the frequency width', default=4)
    parser_create.add_argument('-n', '--nodes', type=int, help='the number of nodes', default=1)
    parser_create.add_argument('-s', '--shutdown', action="store_true", help='add a shutdown drop')
    parser_create.set_defaults(func=command_create)

    parser_use = subparsers.add_parser('use', help='use what is running and deploy')
    parser_use.add_argument('bucket', help='the bucket to access')
    parser_use.add_argument('volume', help='the directory on the host to bind to the Docker Apps')
    parser_use.add_argument('host', help='the host the dfms is running on')
    parser_use.add_argument('-p', '--port', type=int, help='the port to bind to', default=DIM_PORT)
    parser_use.add_argument('-w', '--width', type=int, help='the frequency width', default=4)
    parser_use.add_argument('-s', '--shutdown', action="store_true", help='add a shutdown drop')
    parser_use.set_defaults(func=command_use)

    parser_interactive = subparsers.add_parser('interactive', help='prompt the user for parameters and then run')
    parser_interactive.set_defaults(func=command_interactive)

    args = parser.parse_args()
    return args


if __name__ == '__main__':
    # json 13b-266 /mnt/dfms/dfms_root 8 -w 8 -s
    # interactive
    logging.basicConfig(level=logging.INFO)
    arguments = parser_arguments()
    arguments.func(arguments)