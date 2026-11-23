# Example of a user image (week 6, D18): everything the protocol needs is
# installed here, on top of the testbed image. The testbed installs nothing.
#   docker build -t drone-echo:latest examples/echo
FROM drone:latest

COPY echo_client.py /usr/local/bin/echo_client
COPY echo_server.py /usr/local/bin/echo_server
RUN chmod 755 /usr/local/bin/echo_client /usr/local/bin/echo_server
