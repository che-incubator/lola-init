FROM docker.io/library/python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && git config --system --add safe.directory '*'
# Lola: agent-plugin (plugin.json) support is on main and unreleased; the newest tag, v0.7.1, has none. Pin a commit.
RUN pip install --no-cache-dir "git+https://github.com/LobsterTrap/lola@53b3687975b373c856fc67b9a8f18c395c76fca4"
COPY adapt_mcp.py /opt/adapt_mcp.py
COPY lola_warnings.py /opt/lola_warnings.py
COPY init.sh /opt/init.sh
ENV HOME=/home/user
WORKDIR /home/user
ENTRYPOINT ["/bin/sh","/opt/init.sh"]
