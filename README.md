# Pesto - Bazel Dependency Manager

Pesto is a simple dependency manager for Bazel. In order to use Pesto, you'll need to add a `bazel_spec.json` file at the root of your workspace with the following contents:

```
{
  "name": "pesto_sample_project",
  "version": "0.0.1",
  "doc": "Sample project that uses Pesto for dependencies.",
  "bazel_compatible": "0.21.0",
  "deps": [<dependencies>]
}
```

In addition, you'll need to add the following at the top of your `WORKSPACE` file:

```
load("@bazel_tools//tools/build_defs/repo:git.bzl", "git_repository")

git_repository(
    name = "pesto",
    remote = "https://github.com/sergiocampama/pesto",
    tag = "0.0.1",
)

load("@pesto//:pesto.bzl", "pesto_setup")
pesto_setup()

load("@pesto_generated//:load.bzl", "pesto_load")
pesto_load()

load("@pesto_generated//:init.bzl", "pesto_init")
pesto_init()
```

This is all the setup required to start using Pesto.

## Dependencies

Dependencies can be either remote Git repositories or local dependencies. To declare a remote
dependency, add it like this:

```
{
  ...,
  "deps": [
    {
      "url": "https://github.com/remote/dependency",
      "from": "0.0.1"
    }
  ]
}
```

The `url` field points to a remote repository containing a Pesto enabled dependency. The `from`
field specifies the version requested from that dependency. This version is resolved by means of Git
tags. Tags are automatically created when creating a Release in Github, for example.

Local dependencies are declared using absolute paths:

```
{
  ...,
  "deps": [
    {
      "path": "/path/to/local/dependency"
    }
  ]
}
```

Local dependencies override any remote dependency. Local dependencies can only be declared in the
root manifest file (i.e. the manifest file of the workspace where Bazel is being run). These are
mostly used for development or debugging purposes.

## Example

Check out the [Pesto Sample Project](https://github.com/sergiocampama/pesto_sample_project) for
instructions on how to try Pesto out.
