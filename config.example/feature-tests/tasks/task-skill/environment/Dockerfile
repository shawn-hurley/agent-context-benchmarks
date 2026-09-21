FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends bash git curl ca-certificates libstdc++6 procps nodejs npm && rm -rf /var/lib/apt/lists/*
WORKDIR /work
RUN printf 'broken\n' > answer.txt && printf 'def feature_canary():\n    return True\n' > example.py && git init -q && git -c user.name=Fixture -c user.email=fixture@example.invalid add . && git -c user.name=Fixture -c user.email=fixture@example.invalid commit -qm fixture

COPY task-skills/ /task-skills/
