ARG BUILD_FROM
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/dynprice

RUN apk add --no-cache tzdata

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY dynprice /opt/dynprice/dynprice
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
