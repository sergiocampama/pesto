#!/usr/bin/python
# Copyright 2019 The Bazel Authors. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from collections import defaultdict

import json
import os
import re
import subprocess
import sys


class LocalDepedencyNotAllowed(Exception):
    def __init__(self, message):
        super(LocalDepedencyNotAllowed, self).__init__(message)


def _InvokeSystemCommand(args, inputstr=None):
    proc = subprocess.Popen(args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)
    stdout, stderr = proc.communicate(input=inputstr)
    if proc.returncode != 0:
        print(stderr)
        raise subprocess.CalledProcessError(proc.returncode, args)
    return stdout, stderr


class GitRepo:
    def __init__(self, url):
        self._url = url
        self._name = os.path.basename(url)
        self._revisions = {}
        self._manifests = {}

        self._update()

    @property
    def revisions(self):
        return self._revisions

    @property
    def versions(self):
        return self._revisions.keys()

    def _update(self):
        if not os.path.exists(self._name):
            print("Cloning repo {}...".format(self._url))
            _InvokeSystemCommand(["git", "clone", self._url, self._name])
        else:
            print("Updating repo {}...".format(self._url))
            _InvokeSystemCommand(["git",  "-C", self._name, "pull", "origin", "master"])

        stdout, stderr = _InvokeSystemCommand(["git", "-C", self._name, "show-ref", "--tags"])
        for ref in stdout.split("\n"):
            if ref:
                m = re.search("^(\S+) refs/tags/(.*)$", ref)
                self._revisions[m.group(2)] = m.group(1)

    def manifestAtVersion(self, version):
        if version in self._manifests:
            return self._manifests[version]

        _InvokeSystemCommand(["git", "-C", self._name, "checkout", self._revisions[version]])
        manifest = None
        with open(os.path.join(self._name, "bazel_spec.json")) as contents:
            manifest = Manifest(contents)
        self._manifests[version] = manifest

        return manifest

    def revisionForVersion(self, version):
        return self._revisions[version]


class GitResolver:
    def  __init__(self):
        self._cache = {}

    def getRepo(self, url):
        if url in self._cache:
            return self._cache[url]

        self._cache[url] = GitRepo(url)
        return self._cache[url]

class LocalRepo:
    def __init__(self, path):
        self._path = path
        self._manifest = None

    @property
    def manifest(self):
        if self._manifest:
            return self._manifest
        with open(os.path.join(self._path, "bazel_spec.json")) as contents:
            self._manifest = Manifest(contents)
        return self._manifest



class Manifest:
    def __init__(self, file, is_root=False):
        self._deps = []
        self._initializer = None
        self._local_deps_allowed = is_root
        self._name = None
        self._version = None
        self._bazel_compatible = None
        self._doc = None

        self._parse_manifest(file)

    def _parse_manifest(self, file):
        manifest_data = json.load(file)
        deps = []
        self._name = manifest_data["name"]
        manifest_deps = manifest_data.get("deps", [])
        for dep_data in manifest_deps:
            if "url" in dep_data:
                deps.append(
                    ManifestRemoteDependency(
                        url=dep_data["url"],
                        fromVersion=dep_data["from"],
                    )
                )
            elif "path" in dep_data:
                if self._local_deps_allowed:
                    deps.append(
                        ManifestLocalDependency(
                            path=dep_data["path"],
                        )
                    )
                else:
                    raise LocalDepedencyNotAllowed(
                        "Local dependencies are only allowed at root manifests.",
                    )
            else:
                print("Dependency type {} not supported.".format(str(dep_data)))
        self._deps = deps

        if "initializer" in manifest_data:
            self._initializer = ManifestInitializer(
                path=manifest_data["initializer"]["path"],
                method=manifest_data["initializer"]["method"],
            )

    @property
    def name(self):
        return self._name

    @property
    def dependencies(self):
        return self._deps

    @property
    def initializer(self):
        return self._initializer


class ManifestInitializer:
    def __init__(self, path, method):
        self._path = path
        self._method = method

    @property
    def path(self):
        return self._path

    @property
    def method(self):
        return self._method


class ManifestLocalDependency:
    def __init__(self, path):
      self._path = path

    @property
    def path(self):
        return self._path


class ManifestRemoteDependency:
    def __init__(self, url, fromVersion):
        self._url = url
        self._fromVersion = fromVersion

    @property
    def url(self):
        return self._url

    @property
    def fromVersion(self):
        return self._fromVersion


class ResolvedLocalVersion:
    def __init__(self, name, path, initializer):
        self._name = name
        self._path = path
        self._initializer = initializer

    @property
    def name(self):
        return self._name

    @property
    def path(self):
        return self._path

    @property
    def initializer(self):
        return self._initializer

class ResolvedRemoteVersion:
    def __init__(self, name, url, version, revision, initializer):
        self._name = name
        self._url = url
        self._version = version
        self._revision = revision
        self._initializer = initializer

    @property
    def initializer(self):
        return self._initializer

    @property
    def name(self):
        return self._name

    @property
    def url(self):
        return self._url

    @property
    def revision(self):
        return self._revision


class DependencyGraphCollector:
    def __init__(self):
        self._dependencies = defaultdict(list)
        self._git_resolver = GitResolver()

    def collect(self, deps):
        transitive_deps = []
        if not deps:
            return

        for dep in deps:
            if isinstance(dep, ManifestLocalDependency):
                local_repo = LocalRepo(dep.path)
                dep_manifest = local_repo.manifest
                self._dependencies[dep_manifest.name].append(
                    ResolvedLocalVersion(
                        name=dep_manifest.name,
                        path=dep.path,
                        initializer=dep_manifest.initializer,
                    ),
                )
                transitive_deps.extend(dep_manifest.dependencies)
            else:
                git_repo = self._git_resolver.getRepo(dep.url)
                revision = git_repo.revisionForVersion(dep.fromVersion)

                dep_manifest = git_repo.manifestAtVersion(dep.fromVersion)

                self._dependencies[dep_manifest.name].append(
                    ResolvedRemoteVersion(
                        name=dep_manifest.name,
                        url=dep.url,
                        version=dep.fromVersion,
                        revision=revision,
                        initializer=dep_manifest.initializer,
                    ),
                )
                transitive_deps.extend(dep_manifest.dependencies)

        if transitive_deps:
            self.collect(transitive_deps)

    @property
    def collected(self):
        return self._dependencies


class DependencyResolver:
    def __init__(self, deps):
        self._deps = deps

    def resolved(self):
        resolved = []
        for dep_name, versions in self._deps.items():
            local_found = False
            # TODO(kaipi): Validate that there's only 1 local repo for a specific dependency.
            for version in versions:
                if isinstance(version, ResolvedLocalVersion):
                    resolved.append(version)
                    local_found = True
                    break

            if not local_found:
                # TODO(kaipi): Implement proper version resolution.
                resolved.append(versions[0])
        return resolved


class Printer:
    def _load_statement(self, dep):
        if isinstance(dep, ResolvedLocalVersion):
            return "local_repository(name = \"{name}\", path = \"{path}\")".format(
                name=dep.name,
                path=dep.path,
            )
        return "git_repository(name = \"{name}\", remote = \"{remote}\", commit = \"{revision}\")".format(
            name=dep.name,
            remote=dep.url,
            revision=dep.revision,
        )

    def printLoadsFile(self, deps):
        contents = "\n".join(["    {}".format(self._load_statement(x)) for x in deps])
        return """# This file is generated. Do not modify.

load("@bazel_tools//tools/build_defs/repo:git.bzl", "git_repository")

def pesto_load():
{}
""".format(contents)

    def _initializer_load(self, initializer):
        return "load(\"{path}\", \"{method}\")".format(
            path=initializer.path,
            method=initializer.method,
        )

    def printInitializerFile(self, deps):
        loads = "\n".join([self._initializer_load(x.initializer) for x in deps if x.initializer])

        methods = "\n".join(["    {}()".format(x.initializer.method) for x in deps if x.initializer])

        return """# This file is generated. Do not modify.

{loads}

def pesto_init():
{methods}
""".format(loads=loads, methods=methods)

class Driver:
    def run(self, args):
        manifest = None
        with open(args[0]) as contents:
            manifest = Manifest(contents, is_root=True)

        deps_collector = DependencyGraphCollector()
        deps_collector.collect(manifest.dependencies)

        deps_resolver = DependencyResolver(deps_collector.collected)
        resolved_deps = deps_resolver.resolved()

        printer = Printer()

        loads_file = open("load.bzl", "w")
        loads_file.write(printer.printLoadsFile(resolved_deps))

        initializers_file = open("init.bzl", "w")
        initializers_file.write(printer.printInitializerFile(resolved_deps))
        return 0

if __name__ == "__main__":
    driver = Driver()
    sys.exit(driver.run(sys.argv[1:]))
