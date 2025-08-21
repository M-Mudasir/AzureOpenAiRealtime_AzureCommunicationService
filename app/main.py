import logging
import uuid
import os
from urllib.parse import urlencode, urlparse, urlunparse
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.websockets import WebSocketDisconnect
from azure.eventgrid import EventGridEvent, SystemEventNames
from azure.communication.callautomation import (
    MediaStreamingOptions,
    AudioFormat,
    MediaStreamingContentType,
    MediaStreamingAudioChannelType,
    StreamingTransportType,
)
from azure.communication.callautomation.aio import CallAutomationClient

from app.azureOpenAIService import OpenAIRTHandler

ACS_CONNECTION_STRING = os.getenv("ACS_CONNECTION_STRING")
CALLBACK_URI_HOST = os.getenv("CALLBACK_URI_HOST")  
CALLBACK_EVENTS_URI = CALLBACK_URI_HOST + "/api/callbacks"

logger = logging.getLogger("uvicorn.error")

acs_client = CallAutomationClient.from_connection_string(ACS_CONNECTION_STRING)
app = FastAPI()


@app.post("/api/incomingCall")
async def incoming_call_handler(request: Request) -> Response:
    logger.info("incoming event data")
    events = await request.json()
    if isinstance(events, dict):
        events = [events]

    for event_dict in events:
        event = EventGridEvent.from_dict(event_dict)
        logger.info("incoming event data --> %s", event.data)
        if event.event_type == SystemEventNames.EventGridSubscriptionValidationEventName:
            logger.info("Validating subscription")
            validation_code = event.data["validationCode"]
            validation_response = {"validationResponse": validation_code}
            return JSONResponse(content=validation_response, status_code=200)
        elif event.event_type == "Microsoft.Communication.IncomingCall":
            logger.info("Incoming call received: data=%s", event.data)
            if event.data["from"]["kind"] == "phoneNumber":
                caller_id = event.data["from"]["phoneNumber"]["value"]
            else:
                caller_id = event.data["from"]["rawId"]
            logger.info("incoming call handler caller id: %s", caller_id)
            incoming_call_context = event.data["incomingCallContext"]
            guid = uuid.uuid4()
            query_parameters = urlencode({"callerId": caller_id})
            callback_uri = f"{CALLBACK_EVENTS_URI}/{guid}?{query_parameters}"

            parsed_url = urlparse(CALLBACK_EVENTS_URI)
            websocket_url = urlunparse(("wss", parsed_url.netloc, "/ws", "", "", ""))

            logger.info("callback url: %s", callback_uri)
            logger.info("websocket url: %s", websocket_url)

            media_streaming_options = MediaStreamingOptions(
                transport_url=websocket_url,
                transport_type=StreamingTransportType.WEBSOCKET,
                content_type=MediaStreamingContentType.AUDIO,
                audio_channel_type=MediaStreamingAudioChannelType.MIXED,
                start_media_streaming=True,
                enable_bidirectional=True,
                audio_format=AudioFormat.PCM24_K_MONO,
            )

            answer_call_result = await acs_client.answer_call(
                incoming_call_context=incoming_call_context,
                operation_context="incomingCall",
                callback_url=callback_uri,
                media_streaming=media_streaming_options,
            )
            logger.info(
                "Answered call for connection id: %s", answer_call_result.call_connection_id
            )
    return Response(status_code=200)


@app.post("/api/callbacks/{contextId}")
async def callbacks(contextId: str, request: Request) -> Response:  # noqa: N803
    events = await request.json()
    if isinstance(events, dict):
        events = [events]

    for event in events:
        # Parsing callback events
        event_data = event["data"]
        call_connection_id = event_data["callConnectionId"]
        logger.info(
            f"Received Event:-> {event['type']}, Correlation Id:-> {event_data['correlationId']}, CallConnectionId:-> {call_connection_id}"
        )
        if event["type"] == "Microsoft.Communication.CallConnected":
            call_connection_properties = (
                await acs_client.get_call_connection(call_connection_id).get_call_properties()
            )
            media_streaming_subscription = call_connection_properties.media_streaming_subscription
            logger.info(
                f"MediaStreamingSubscription:--> {media_streaming_subscription}"
            )
            logger.info(
                f"Received CallConnected event for connection id: {call_connection_id}"
            )
            logger.info("CORRELATION ID:--> %s", event_data["correlationId"])
            logger.info("CALL CONNECTION ID:--> %s", event_data["callConnectionId"])
        elif event["type"] == "Microsoft.Communication.MediaStreamingStarted":
            logger.info(
                f"Media streaming content type:--> {event_data['mediaStreamingUpdate']['contentType']}"
            )
            logger.info(
                f"Media streaming status:--> {event_data['mediaStreamingUpdate']['mediaStreamingStatus']}"
            )
            logger.info(
                f"Media streaming status details:--> {event_data['mediaStreamingUpdate']['mediaStreamingStatusDetails']}"
            )
        elif event["type"] == "Microsoft.Communication.MediaStreamingStopped":
            logger.info(
                f"Media streaming content type:--> {event_data['mediaStreamingUpdate']['contentType']}"
            )
            logger.info(
                f"Media streaming status:--> {event_data['mediaStreamingUpdate']['mediaStreamingStatus']}"
            )
            logger.info(
                f"Media streaming status details:--> {event_data['mediaStreamingUpdate']['mediaStreamingStatusDetails']}"
            )
        elif event["type"] == "Microsoft.Communication.MediaStreamingFailed":
            logger.info(
                f"Code:->{event_data['resultInformation']['code']}, Subcode:-> {event_data['resultInformation']['subCode']}"
            )
            logger.info(f"Message:->{event_data['resultInformation']['message']}")
        elif event["type"] == "Microsoft.Communication.CallDisconnected":
            pass
    return Response(status_code=200)


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    handler = OpenAIRTHandler()
    print("Client connected to WebSocket")
    await handler.init_incoming_websocket(websocket)
    await handler.start_client()
    while True:
        try:
            data = await websocket.receive_text()
            await handler.acs_to_oai(data)
            await handler.send_welcome()
        except WebSocketDisconnect:
            break
        except Exception as e:  # pylint: disable=broad-except
            print(f"WebSocket connection closed: {e}")
            break


@app.get("/")
async def home() -> PlainTextResponse:
    return PlainTextResponse("Hello ACS CallAutomation!")
