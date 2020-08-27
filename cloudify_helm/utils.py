########
# Copyright (c) 2019 Cloudify Platform Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
#    * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    * See the License for the specific language governing permissions and
#    * limitations under the License.

import os
import shutil
import tarfile
import tempfile
from contextlib import contextmanager

from cloudify.exceptions import NonRecoverableError

from helm_sdk import Helm
from helm_sdk.utils import run_subprocess
from .constants import (
    CLIENT_CONFIG,
    EXECUTABLE_PATH,
    RESOURCE_CONFIG,
    DATA_DIR_ENV_VAR,
    CACHE_DIR_ENV_VAR,
    CONFIG_DIR_ENV_VAR,
    HELM_ENV_VARS_LIST,
    USE_EXTERNAL_RESOURCE)


def helm_from_ctx(ctx):
    # Look for executable path in runtime property or in default place.
    executable_path = ctx.instance.runtime_properties.get(
        EXECUTABLE_PATH, "") or ctx.node.properties.get(
        'helm_config', {}).get(EXECUTABLE_PATH, "")
    if not os.path.exists(executable_path):
        raise NonRecoverableError(
            "Helm's executable not found in {0}. Please set the "
            "'executable_path' property accordingly.".format(
                executable_path))
    env_variables = get_helm_env_vars_dict(ctx)
    helm = Helm(
        ctx.logger,
        executable_path,
        environment_variables=env_variables)
    return helm


def get_helm_env_vars_dict(ctx):
    env_vars = {}
    for property_name in HELM_ENV_VARS_LIST:
        env_var_value = ctx.instance.runtime_properties.get(property_name, "")
        if not env_var_value:
            raise NonRecoverableError(
                "ctx of node {node_id} must have helm env variables {name}!, "
                "use run_on_host relationship.".format(
                    node_id=ctx.node.id, name=property_name))
        env_vars[property_name] = ctx.instance.runtime_properties.get(
            property_name)
    return env_vars


def is_using_existing(ctx):
    return ctx.node.properties.get(
        'use_existing_resource', True)


@contextmanager
def get_kubeconfig_file(ctx):
    if ctx.node.properties.get(CLIENT_CONFIG, {}).get('kube_config'):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.close()
            ctx.download_resource(
                ctx.node.properties.get(CLIENT_CONFIG).get('kube_config'),
                target_path=f.name)
            try:
                yield f.name
            finally:
                os.remove(f.name)
    else:
        yield None


@contextmanager
def get_values_file(ctx):
    if ctx.node.properties.get(RESOURCE_CONFIG, {}).get('values_file'):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.close()
            ctx.download_resource(
                ctx.node.properties.get(RESOURCE_CONFIG).get('values_file'),
                target_path=f.name)
            try:
                yield f.name
            finally:
                os.remove(f.name)
    else:
        yield None


@contextmanager
def get_binary(ctx):
    installation_temp_dir = tempfile.mkdtemp()
    installation_source = \
        ctx.node.properties.get(
            'installation_source', "")
    if not installation_source:
        raise NonRecoverableError(
            "invalid installation_source")
    installation_tar = \
        os.path.join(installation_temp_dir, 'helm.tar.gz')

    ctx.logger.info(
        "Downloading Helm from {0} into {1}".format(
            installation_source, installation_tar))
    run_subprocess(
        ['curl', '-o', installation_tar, installation_source],
        ctx.logger
    )
    untar_and_set_permissions(ctx,
                              installation_tar,
                              installation_temp_dir)
    # Need to find helm binary in the extracted files
    binary = find_binary(installation_temp_dir)
    try:
        yield binary
    finally:
        shutil.rmtree(installation_temp_dir)


def untar_and_set_permissions(ctx, tar_file, target_dir):
    ctx.logger.info("Untarring into {0}".format(target_dir))
    with tarfile.open(tar_file, 'r') as tar_ref:
        for name in tar_ref.getnames():
            tar_ref.extract(name, target_dir)
            target_file = os.path.join(target_dir, name)
            ctx.logger.info(
                "Setting permission on {0}".format(target_file))
            run_subprocess(
                ['chmod', 'u+x', target_file],
                ctx.logger
            )


def find_binary(source_dir):
    for root, dir, filenames in os.walk(source_dir):
        for file in filenames:
            if file.endswith('helm'):
                return os.path.join(root, file)


def copy_binary(source, dest):
    if not os.path.isdir(os.path.dirname(dest)):
        os.makedirs(os.path.dirname(dest))
    try:
        shutil.copy2(source, dest)
    except Exception as e:
        raise NonRecoverableError(
            "failed to copy binary: {}".format(e))


def use_existing_repo_on_helm(ctx, helm):
    """
    Check if a repo that user asked for in resource_config exists on helm
    client.
    :param ctx: cloudify context.
    :param helm: helm client object.
    :return Nothing, raises NonRecoverableError exception if repository
    doesen't exist.
    """
    if ctx.node.properties.get(USE_EXTERNAL_RESOURCE):
        repos_list = helm.repo_list()
        resource_config = ctx.node.properties.get(RESOURCE_CONFIG, {})
        for repo in repos_list:
            if repo.get('name') == resource_config.get('name') and \
                    repo.get('url') == resource_config.get('repo_url'):
                return True
        raise NonRecoverableError(
            "cant find repository:{0} with url: {1} on helm clinet!".format(
                resource_config.get('name'), resource_config.get('repo_url')))


def create_temporary_env_of_helm(ctx):
    """
    Create temporary directories for helm cache,data and configuration files
    and inject their paths to runtime properties.
    :param ctx: cloudify context.

    """
    ctx.instance.runtime_properties[CACHE_DIR_ENV_VAR] = tempfile.mkdtemp()
    ctx.instance.runtime_properties[CONFIG_DIR_ENV_VAR] = tempfile.mkdtemp()
    ctx.instance.runtime_properties[DATA_DIR_ENV_VAR] = tempfile.mkdtemp()


def delete_temporary_env_of_helm(ctx):
    for dir_property_name in HELM_ENV_VARS_LIST:
        dir_to_delete = ctx.instance.runtime_properties.get(
            dir_property_name, "")
        if os.path.isdir(dir_to_delete):
            ctx.logger.info("Removing: {}".format(dir_to_delete))
            shutil.rmtree(dir_to_delete)
        else:
            ctx.logger.info(
                "Directory {0} doesn't exist,skipping".format(dir_to_delete))
