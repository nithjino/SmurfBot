FROM python:3.7-slim-buster
RUN pip install GroupyAPI==0.10.3
RUN pip install atomicwrites==1.3.0
RUN useradd --create-home groupme
WORKDIR /home/groupme
USER groupme
RUN mkdir app/
RUN mkdir app/src/
RUN mkdir app/creds/
RUN mkdir app/logs/
RUN mkdir app/tags/
COPY src/* app/src/
CMD ["python","app/src/bot_start.py"]
