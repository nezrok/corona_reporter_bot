FROM alpine:3.12

# Create and set the working directory.
WORKDIR /

RUN apk upgrade --update-cache --available && apk add --update build-base python3-dev libffi-dev py-pip openssl-dev

# Only copy requirements.txt and install the dependencies.
COPY ./requirements.txt .
RUN pip3 install -r requirements.txt

# Copy all other files files.
COPY . .

CMD ["python3", "./src/corona_reporter_bot.py"]
