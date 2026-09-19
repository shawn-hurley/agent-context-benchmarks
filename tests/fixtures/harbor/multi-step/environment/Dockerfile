# Build the local prerequisite using Dockerfile.rtk-smoke before this live fixture.
FROM localhost/acb-rtk-smoke:0.48.0
COPY server.py /fixture-server.py
WORKDIR /work
RUN printf 'broken\n' > answer.txt && git init -q && git -c user.name=Fixture -c user.email=fixture@example.invalid commit --allow-empty -qm fixture
