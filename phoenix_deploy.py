#!/usr/bin/env python3

import socket
import signal
import json
import logging
import logging.handlers
import subprocess
import argparse
import random
from time import sleep
from multiprocessing import Process

parser = argparse.ArgumentParser(description="Deploy Docker")

parser.add_argument("--service-source", dest="service_source", required=True)
parser.add_argument("--docker-repo", dest="docker_repo", required=True)
parser.add_argument("--env", dest="phoenix_env", required=True)
parser.add_argument("--cluster-path", dest="cluster_path", required=True)

parser.add_argument("--no-loop", dest="loop", action="store_false")
parser.add_argument("-v", dest="verbose", action="store_true")

parser.add_argument("--max-speed", dest="max_speed", type=int, default=30)
parser.add_argument("--min-speed", dest="min_speed", type=int, default=10)
parser.add_argument("--default-services", dest="default_services", default=[], nargs="*")
parser.add_argument("--cluster-name", dest="cluster_name", help="cluster to operate as")
parser.add_argument("--service-names", dest="service_names", help="service names to deploy", nargs="*", default=[])

logging.basicConfig(level=logging.INFO, format='docker_deploy %(levelname)s [%(threadName)s:%(module)s.py:%(lineno)d] %(message)s')
logger = logging.getLogger('docker_deploy')

def call(command):
    return subprocess.run(command, stdout=subprocess.PIPE, check=True)

class DockerUtils():
    @classmethod
    def system_prune(cls):
        docker_rmi_cmd = ['/usr/bin/docker', 'system', 'prune', '-af']
        call(docker_rmi_cmd)

    @classmethod
    def check_image(cls, service):
        service_name = service.name
        version = service.version

        service_running, running_version = cls.get_running_status_for_service(service_name)

        if service_running and running_version == version:
            return False

        return True

    @classmethod
    def get_running_status_for_service(cls, service_name):
        cmd = ['/usr/bin/docker', 'inspect', service_name]
        try:
            proc = call(cmd)

            stdout = proc.stdout.decode('utf-8').strip()
            inspected = json.loads(stdout)[0] #inspect returns array of 1 object
            running_version = inspected['Config']['Image'].split(":")[1]
            return inspected['State']['Running'], running_version
        except (subprocess.CalledProcessError, KeyError):
            return None, None

    @classmethod
    def list_running_containers(cls):
        docker_ps_cmd = ['/usr/bin/docker', 'container', 'ls', '--format', "{{ .Names }}"]
        try:
            proc = call(docker_ps_cmd)
            stdout = proc.stdout.decode('utf-8').strip()
            return stdout.split('\n')
        except subprocess.CalledProcessError:
            return []

class Service():
    '''
    Service
    {
        "name": "dispatcher-server",
        "version": "master-2018121800421545093776",
        "env_args": [],
        "volumes": [],
        "live_deploy": true,
    }
    '''
    def __init__(self, service_json):
        self.name = service_json['name']
        self.version = service_json['version']
        self.env_args = service_json.get('env_args', [])
        self.volumes = service_json.get('volumes', [])
        self.live_deploy = service_json.get('live_deploy', True)

class Cluster():
    '''
    Cluster
    {
        "name": "admin",
        "service_names": ["etcd", "wildling", "phoenix"],
        "http_proxy_port": 1000
    }
    '''
    def __init__(self, cluster_json):
        self.name = cluster_json['name']
        self.service_names = cluster_json['service_names']
        self.http_proxy_port = cluster_json['http_proxy_port']

class PhoenixDeploy():
    def __init__(self, _args):
        self.args = _args
        self.cluster_name = self.args.cluster_name or socket.getfqdn().split("-")[1]

    def _get_cluster(self):
        try:
            with open(args.cluster_path + '/' + self.cluster_name, 'r') as f:
                cluster = Cluster(json.load(f))
                return cluster
        except FileNotFoundError:
            return None

    def _get_service(self, service_name):
        try:
            with open(args.service_path + '/' + service_name, 'r') as f:
                service = Service(json.load(f))
                return service
        except FileNotFoundError:
            return None

    def start(self):
        run = self.args.loop
        speed = random.randint(self.args.min_speed, self.args.max_speed)

        def sigint_handler(*_):
            nonlocal run
            logger.info('Got SIGINT')
            run = False

        signal.signal(signal.SIGINT, sigint_handler)
        self.run()
        logger.debug('initial run completed')
        while run:
            self.run()
            logger.debug('run completed -- sleeping %s seconds' % speed)
            sleep(speed)

    def run(self):
        try:
            self.deploy_services()
            self.clean_up_extra_services()
        except Exception:
            logger.exception('Uncaught exception in main run loop')

    def deploy_services(self):
        service_names = self.get_service_names()

        if not service_names:
            logger.debug("no services for cluster %s" % self.cluster_name)
            return

        #  logger.info('service_names: {}'.format(service_names))
        deploy_procs = []
        for service_name in service_names:
            proc = self.deploy_service(service_name)
            if proc:
                deploy_procs.append(proc)

        for proc in deploy_procs:
            proc.join()

        DockerUtils.system_prune()

    def get_service_names(self):
        service_names = self.args.service_names
        if not service_names:
            cluster = self._get_cluster()
            if cluster:
                service_names = cluster.service_names
                # TODO figure out stencil equivalent
                #  if cluster.get('http_proxy_port', 0) != 0 and 'stencil' not in service_names:
                    #  service_names.append('stencil')

        service_names.extend(self.args.default_services)
        return list(set(service_names))

    def deploy_service(self, service_name):
        try:
            service = self._get_service(service_name)
            #  logger.debug('Got {}: {}'.format(service_name, service))
            if service:
                do_deploy = DockerUtils.check_image(service)
                if do_deploy:
                    logger.info('Deploying {}'.format(service_name))
                    proc = Process(target=self.pull_and_run, args=(self, service,))
                    proc.start()
                    return proc
            else:
                logger.warning('{} Does not exist so not deploying'.format(service_name))

        except Exception:
            logger.exception("couldn't deploy: {}".format(service_name))

        return None

    def pull_and_run(self, service):
        self.docker_pull(service)
        self.docker_run(service)

    def docker_pull(self, service):
        """
        Ensure the proper version of the service is loaded into docker
        """
        service_name = service.name
        version = service.version

        image = self.args.docker_repo + '/' + service_name + ':' + version
        logger.info("Pulling {} ({}) from Docker registry".format(service_name, image))
        pull_cmd = ['/usr/bin/docker', 'pull', image]

        call(pull_cmd)

    def docker_run(self, service):
        """
        Ensure the service is running, with the proper network, env, volumes, etc.
        """
        service_name = service.name
        version = service.version
        image = self.args.docker_repo + '/' + service_name + ':' + version

        # TODO figure out how to get health
        #  live_deploy = service.live_deploy
        #  if not live_deploy:
            #  stoppable = EtcD.get_health_status().get('stoppable', False)
            #  service_running, _ = get_running_status_for_service(service_name)
            #  if service_running and not stoppable:
                #  return

        try:
            remove_cmd = ['/usr/bin/docker', 'rm', service_name]
            call(remove_cmd)
        except subprocess.CalledProcessError:
            pass

        fqdn = socket.getfqdn()
        env = self.args.phoenix_env

        prefix = fqdn.split(".")[0]
        container_hostname = "{}.{}".format(prefix, service_name)

        run_cmd = [
            '/usr/bin/docker', 'run', '-d',
            '--network', 'host',
            '--hostname', container_hostname,
            '--name', service_name,
            #  '--log-driver', 'syslog',
            #  '--log-opt', 'syslog-format=rfc5424',
            #  '--log-opt', 'syslog-address=udp://localhost:514',
            #  '--log-opt', 'syslog-facility=local4',
            #  '--log-opt', 'tag={}'.format(service_name),
            '-e', 'PHOENIX_ENV={}'.format(env),
            '-v', '/data/dispatcher:/data/dispatcher'
        ]

        for env_addition in service['env_args']:
            run_cmd.extend(['-e', env_addition])

        for volume in service['volumes']:
            run_cmd.extend(['-v', volume])

        run_cmd.append(image)

        logger.info('Running up {} with {}'.format(service_name, run_cmd))

        call(run_cmd)

    def clean_up_extra_services(self):
        expected_service_names = self.get_service_names()
        non_default_service_names = [service_name for service_name in expected_service_names if service_name not in self.args.default_services]
        logger.debug('expected_service_names: %s, non_default_service_names: %s' % (expected_service_names, non_default_service_names))

        if not non_default_service_names:
            return

        running_service_names = DockerUtils.list_running_containers()

        to_delete = [service_name for service_name in running_service_names if service_name not in expected_service_names]

        if not to_delete:
            return

        logger.info('deleting services: {}'.format(to_delete))

        delete_cmd = ['/usr/bin/docker', 'rm', '-f']
        delete_cmd.extend(to_delete)
        call(delete_cmd)

if __name__ == '__main__':
    args = parser.parse_args()
    if args.verbose:
        logger.setLevel(logging.DEBUG)
    PhoenixDeploy(args).start()
