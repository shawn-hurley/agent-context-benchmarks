# ScarfBench Maven dependency caching

ScarfBench runs enable persistent Maven download caching by default. This includes
Kantra agent containers and native ScarfBench verification. Cache storage lives in
the selected container engine, so macOS Podman does not require sharing a host
`.m2` directory with its VM.

## Configuration

```yaml
overrides:
  benchmark:
    maven_cache: true
    maven_cache_volume: acb-scarfbench-maven-v1
```

`maven_cache` defaults to `true`; `maven_cache_volume` defaults to
`acb-scarfbench-maven-v1`. Set `maven_cache: false` for a run without this feature.
Use a different name for a separate cache namespace. These options apply to fresh
prepared tasks; rerunning a previously prepared plan does not rewrite its images.

## Storage and isolation

Maven Resolver's split repository separates downloaded artifacts under
`/root/.m2/repository/cached` from locally installed application artifacts under
`/root/.m2/repository/installed`. Only the downloaded subtree is shared. The
packaged ScarfBench and Kantra images use Maven 3.9; custom images must support
Resolver's split repository and file locks (Resolver 1.9.2 or newer).

Agent and validation runtime containers mount an external named volume at the
cached subtree. The worker creates it before Harbor starts its environments.
Harbor's normal container and trial-volume cleanup leaves this external volume
intact. Maven uses file locks stored on that shared volume to coordinate processes.
Application artifacts built with `mvn install` remain private to their image or
trial. The cache contains dependencies, not application source or credentials.

Image builds use persistent BuildKit/Buildah `RUN --mount=type=cache` storage with
the same configured cache ID. The build cache and runtime named volume are separate
stores: each warms independently. Build cache mounts serialize concurrent build
steps; downloaded content persists across image builds. Local application artifacts
remain in the image's private filesystem. Docker builds require BuildKit, which ACB
enables for ScarfBench. Podman supports these mounts through Buildah.

ACB instruments frozen task recipes and run-local validator snapshots. It does not
rewrite the benchmark checkout's Dockerfiles. Source and behavior tests retain their
original commands. Download caching does not grant network access to offline tasks
or turn an unsuccessful dependency resolution into a passing grade.

## Rate limits and maintenance

A warm cache reduces repeated requests to Maven Central. Uncached artifacts still
require repository access. A current HTTP 429 block cannot be cleared by deleting
the cache; deleting it forces more downloads. Maven Central controls block expiry.
See [Sonatype's rate-limit guidance](https://central.sonatype.org/faq/429-error/).

To inspect runtime cache storage:

```sh
podman volume inspect acb-scarfbench-maven-v1
# Use docker instead when that is the configured engine.
```

To deliberately clear runtime downloads, stop runs using that cache, then remove
its named volume. Build cache storage is managed separately by the container
builder; choose a new `maven_cache_volume` name for a fresh build/runtime namespace.

## Verification

`tests/test_maven_cache.py` checks configuration, opt-out, multistage recipe
instrumentation, external volume lifetime, argument-safe native CLI routing, and
benchmark snapshot isolation. Model-free live checks use a local Maven fixture
repository: download a parent POM, remove the repository, and validate offline in
both a second container and a separate image build. The container check also verifies
that locally installed artifacts from the first container do not appear in the next.

[Maven's repository documentation](https://maven.apache.org/resolver/local-repository.html)
explains downloaded versus installed artifacts and concurrent access coordination.

Run the model-free container check with a locally available Maven 3.9 image:

```sh
uv run python -m scripts.check_scarfbench_maven_cache /tmp/acb-maven-check \
  --engine podman --image docker.io/library/maven:3.9.12-ibm-semeru-21-noble
```

The output directory must be new. The check saves logs and `check.json`, tests
concurrent cold downloads, and removes its containers, images, and runtime volume.
Its uniquely named builder cache remains managed by the container builder.
