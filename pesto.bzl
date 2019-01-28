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

def _resolver_rule_impl(repository_ctx):
    """Repository rule implementation that invokes dependency resolver."""
    spec_path = repository_ctx.path(repository_ctx.attr._spec_file)
    resolver_path = repository_ctx.path(repository_ctx.attr._resolver_script)

    # Create a BUILD file next to where the bzl files are created, to
    # create a Bazel package through which to load the bzl files.
    repository_ctx.file("BUILD", "# I am a teapot, short and stout. Don't touch me")

    result = repository_ctx.execute([resolver_path, spec_path])
    if result.return_code != 0:
        fail("Error while running pesto:\nstdout: {stdout}\nstderr: {stderr}".format(
            stdout = result.stdout,
            stderr = result.stderr,
        ))

_resolver_rule = repository_rule(
    implementation = _resolver_rule_impl,
    attrs = {
        "_spec_file": attr.label(
            allow_single_file = True,
            default = Label("@//:pesto.json")
        ),
        "_resolver_script": attr.label(
            allow_single_file = True,
            default = Label("@pesto//:pesto.py"),
        ),
    },
)

def pesto_setup():
    """Macro to hide the declaration of the repository rule."""
    _resolver_rule(name = "pesto_generated")
