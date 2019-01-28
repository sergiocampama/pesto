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
from functools import total_ordering
import os
import re
import subprocess
import sys


class LocalDepedencyNotAllowedError(Exception):
    def __init__(self, message):
        super(LocalDepedencyNotAllowedError, self).__init__(message)

class VersionParseError(Exception):
    def __init__(self, message):
        super(VersionParseError, self).__init__(message)


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

_COMPONENT_REGEX = re.compile("^(\d+)(?:([a-z]+)(\d*))?$")

@total_ordering
class DottedVersionComponent:
    def __init__(self, component_str):
        self._original = component_str
        match = _COMPONENT_REGEX.search(component_str)

        self._first_number = int(match.group(1))
        self._string = match.group(2)
        self._second_number = match.group(3) or 0

    def __eq__(self, other):
        return (self._first_number == other._first_number and
                self._string == other._string and
                self._second_number == other._second_number)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __gt__(self, other):
        if self._first_number != other._first_number:
            return self._first_number > other._first_number

        if self._string == other._string:
            return self._second_number > other._second_number

        if self._string and other._string == None:
            return False

        if other._string and self._string == None:
            return True

        return self._string > other._string

    def __hash__(self):
        return hash((self._first_number, self._string, self._second_number))

    def __str__(self):
        return self._original

    def __repr__(self):
        return self._original

    @property
    def next(self):
        if self._string:
            return DottedVersionComponent(str(self._first_number))
        return DottedVersionComponent(str(self._first_number + 1))

ZERO_COMPONENT = DottedVersionComponent("0")

@total_ordering
class DottedVersion:
    def __init__(self, version_str):
        self._components = [DottedVersionComponent(x) for x in version_str.split(".")]

        # Canonicalize by removing trailing zero components.
        while len(self._components) > 0 and self._components[-1] == ZERO_COMPONENT:
            self._components.pop(-1)

        self._components_length = len(self._components)

    def __eq__(self, other):
        # Because the components have been canonicalized, if the lengths differ, it's because
        # they're different.
        if self._components_length != other._components_length:
            return False
        for idx in range(self._components_length):
            if self._components[idx] != other._components[idx]:
                return False
        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    def __gt__(self, other):
        max_component_count = max(self._components_length, other._components_length)
        for idx in range(max_component_count):
            if idx >= self._components_length:
                self_component = ZERO_COMPONENT
            else:
                self_component = self._components[idx]

            if idx >= other._components_length:
                other_component = ZERO_COMPONENT
            else:
                other_component = other._components[idx]

            if self_component != other_component:
                return self_component > other_component

        return False

    def __hash__(self):
        return hash(tuple(self._components))

    def __repr__(self):
        return "<DottedVersion> = {}".format(self.canonical)

    def __str__(self):
        return self.canonical

    @property
    def canonical(self):
        return ".".join([str(x) for x in self._components])

    @property
    def nextMinor(self):
        components = [self._components[0]]
        if self._components_length < 2:
            components.append(ZERO_COMPONENT.next)
        else:
            components.append(self._components[1].next)
        return DottedVersion(".".join([str(x) for x in components]))

    @property
    def nextMajor(self):
        return DottedVersion(str(self._components[0].next))


class DottedVersionRange:
    def __init__(self, lower_bound, upper_bound, upper_bound_inclusive=True):
        self._lower_bound = lower_bound
        self._upper_bound = upper_bound
        self._upper_bound_inclusive = upper_bound_inclusive

    def __repr__(self):
        return "<DottedVersionRange> lower: {lower}, upper: {upper}, inclusive: {inclusive}".format(
            lower=self._lower_bound,
            upper=self._upper_bound,
            inclusive=self._upper_bound_inclusive,
        )

    @property
    def lower_bound(self):
        return self._lower_bound

    def contains(self, version):
        if version < self._lower_bound:
            return False
        if self._upper_bound_inclusive:
            return version <= self._upper_bound
        return version < self._upper_bound

    @staticmethod
    def intersect(first_range, *version_ranges):
        lower_bound = first_range._lower_bound
        upper_bound = first_range._upper_bound
        upper_bound_inclusive = first_range._upper_bound_inclusive

        for version_range in version_ranges:
            if version_range._lower_bound > lower_bound:
                lower_bound = version_range._lower_bound

            # TODO(kaipi): Check well what happens if there are 2 equal upper bounds but different
            # upper_bound_inclusive.
            if version_range._upper_bound < upper_bound:
                upper_bound = version_range._upper_bound
                upper_bound_inclusive = version_range._upper_bound_inclusive

        return DottedVersionRange(
            lower_bound,
            upper_bound,
            upper_bound_inclusive=upper_bound_inclusive,
        )


class GitRepo:
    def __init__(self, url):
        self._url = url
        self._name = os.path.basename(url)
        self._revisions = {}
        self._manifests = {}

        self._update()

    @property
    def url(self):
        return self._url

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
                self._revisions[DottedVersion(m.group(2))] = m.group(1)

    def manifestAtVersion(self, version):
        if version in self._manifests:
            return self._manifests[version]

        _InvokeSystemCommand(["git", "-C", self._name, "checkout", self._revisions[version]])
        manifest = None
        with open(os.path.join(self._name, "pesto.json")) as contents:
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
        with open(os.path.join(self._path, "pesto.json")) as contents:
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
                version_range = None
                if "from" in dep_data:
                    version = DottedVersion(dep_data["from"])
                    version_range = DottedVersionRange(
                        version,
                        version.nextMajor,
                        upper_bound_inclusive=False,
                    )
                elif "up_to_next_major" in dep_data:
                    version = DottedVersion(dep_data["up_to_next_major"])
                    version_range = DottedVersionRange(
                        version,
                        version.nextMajor,
                        upper_bound_inclusive=False,
                    )
                elif "up_to_next_minor" in dep_data:
                    version = DottedVersion(dep_data["up_to_next_minor"])
                    version_range = DottedVersionRange(
                        version,
                        version.nextMinor,
                        upper_bound_inclusive=False,
                    )
                elif "exact" in dep_data:
                    version = DottedVersion(dep_data["exact"])
                    version_range = DottedVersionRange(version, version)
                else:
                    raise VersionParseError("Couldn't find any version declaration.")
                deps.append(
                    ManifestRemoteDependency(
                        url=dep_data["url"],
                        version_range=version_range,
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
                    raise LocalDepedencyNotAllowedError(
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
    def __init__(self, url, version_range):
        self._url = url
        self._version_range = version_range

    @property
    def url(self):
        return self._url

    @property
    def version_range(self):
        return self._version_range


class RequestedLocalVersion:
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

class RequestedRemoteVersion:
    def __init__(self, name, git_repo, version_range):
        self._name = name
        self._git_repo = git_repo
        self._version_range = version_range

    @property
    def name(self):
        return self._name

    @property
    def git_repo(self):
        return self._git_repo

    @property
    def version_range(self):
        return self._version_range

class ResolvedLocalDependency:
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

class ResolvedRemoteDependency:
    def __init__(self, name, url, revision, initializer, version):
        self._name = name
        self._url = url
        self._revision = revision
        self._initializer = initializer
        self._version = version

    @property
    def name(self):
        return self._name

    @property
    def url(self):
        return self._url

    @property
    def revision(self):
        return self._revision

    @property
    def initializer(self):
        return self._initializer

    @property
    def version(self):
        return self._version


class DependencyGraphCollector:
    def __init__(self, git_resolver):
        self._dependencies = defaultdict(list)
        self._git_resolver = git_resolver

    def collect(self, deps):
        transitive_deps = []
        if not deps:
            return

        for dep in deps:
            if isinstance(dep, ManifestLocalDependency):
                local_repo = LocalRepo(dep.path)
                dep_manifest = local_repo.manifest
                self._dependencies[dep_manifest.name].append(
                    RequestedLocalVersion(
                        name=dep_manifest.name,
                        local_repo=local_repo,
                    ),
                )
                transitive_deps.extend(dep_manifest.dependencies)
            else:
                git_repo = self._git_resolver.getRepo(dep.url)
                lower_version = dep.version_range.lower_bound
                dep_manifest = git_repo.manifestAtVersion(lower_version)

                self._dependencies[dep_manifest.name].append(
                    RequestedRemoteVersion(
                        name=dep_manifest.name,
                        git_repo=git_repo,
                        version_range=dep.version_range,
                    ),
                )
                transitive_deps.extend(dep_manifest.dependencies)

        if transitive_deps:
            self.collect(transitive_deps)

    @property
    def collected(self):
        return self._dependencies


class DependencyResolver:
    def __init__(self, git_resolver):
        self._git_resolver = git_resolver

    def resolve(self, deps):
        resolved = []
        for dep_name, requested_versions in deps.items():
            local_found = False
            # TODO(kaipi): Validate that there's only 1 local repo for a specific dependency.
            for version in requested_versions:
                if isinstance(version, RequestedLocalVersion):
                    resolved.append(
                        ResolvedLocalDependency(
                            name=dep_name,
                            path=version.local_repo.path,
                            initializer=version.local_repo.manifest.initializer,
                        )
                    )
                    local_found = True
                    break

            if not local_found:
                version_ranges = [x.version_range for x in requested_versions]
                intersection = DottedVersionRange.intersect(*version_ranges)

                # TODO(kaipi): Validate they don't come from different repos.
                git_repo = requested_versions[0].git_repo
                git_versions = git_repo.versions
                git_versions.sort()

                resolved_version = None

                for version in git_versions:
                    if intersection.contains(version):
                        resolved_version = version
                        break

                # TODO(kaipi): Validate there is a version that we can use.
                resolved.append(
                    ResolvedRemoteDependency(
                        name=dep_name,
                        url=git_repo.url,
                        revision=git_repo.revisionForVersion(resolved_version),
                        initializer=git_repo.manifestAtVersion(resolved_version).initializer,
                        version=resolved_version,
                    ),
                )

        return resolved


class Printer:
    def _version_comment(self, dep):
        if isinstance(dep, ResolvedLocalDependency):
            return "# {name}, path: {path}".format(
                name=dep.name,
                path=dep.path,
            )
        return "# {name}, version: {version}".format(
            name=dep.name,
            version=dep.version,
        )

    def _load_statement(self, dep):
        if isinstance(dep, ResolvedLocalDependency):
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
        dep_versions = "\n".join(["{}".format(self._version_comment(x)) for x in deps])
        dep_loads = "\n".join(["    {}".format(self._load_statement(x)) for x in deps])
        return """# This file is generated. Do not modify.

load("@bazel_tools//tools/build_defs/repo:git.bzl", "git_repository")

{versions}

def pesto_load():
{loads}
""".format(versions=dep_versions, loads=dep_loads)

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

        git_resolver = GitResolver()
        deps_collector = DependencyGraphCollector(git_resolver)
        deps_collector.collect(manifest.dependencies)

        deps_resolver = DependencyResolver(git_resolver)
        resolved_deps = deps_resolver.resolve(deps_collector.collected)

        printer = Printer()

        loads_file = open("load.bzl", "w")
        loads_file.write(printer.printLoadsFile(resolved_deps))

        initializers_file = open("init.bzl", "w")
        initializers_file.write(printer.printInitializerFile(resolved_deps))
        return 0

if __name__ == "__main__":
    driver = Driver()
    sys.exit(driver.run(sys.argv[1:]))
