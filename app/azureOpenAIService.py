import json
import os
from dotenv import load_dotenv

load_dotenv()

from openai import AsyncAzureOpenAI

import asyncio
import json
import httpx

AZURE_OPENAI_API_ENDPOINT = os.getenv("AZURE_OPENAI_API_ENDPOINT")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_DEPLOYMENT_NAME = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
SAMPLE_RATE = 24000

def session_config():
    """Returns a random value from the predefined list."""

    SESSION_CONFIG={
        "input_audio_transcription": {
            "model": "whisper-1",
        },
        "turn_detection": {
            "threshold": 0.4,
            "silence_duration_ms": 600,
            "type": "server_vad"
        },
        "instructions": "Your name is Mudasir, you work for Novizant Services. You're a helpful, calm and cheerful agent who responds with a clam British accent, but also can speak in any language or accent. Always start the conversation with a cheery hello, stating your name and who do you work for! You can also call functions when requested. Ask if the user has any questions or if they need help with anything if not then end the call.",
        "voice": "shimmer",
        "modalities": ["text", "audio"],
        "tool_choice": "auto",
        "tools": [
            {
                "type": "function",
                "name": "get_ticket",
                "description": "Get the ticket from the server",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "ticket_id": {
                            "type": "string",
                            "description": "The id of the ticket to get"
                        }
                    },
                    "required": ["ticket_id"]
                }
            },
            {
                "type": "function",
                "name": "create_ticket",
                "description": "Create a new ticket on the server",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "The description of the ticket to create"
                        }
                    },
                    "required": ["description"]
                }
            },
            {
                "type": "function",
                "name": "end_call",
                "description": "End the current phone call",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        ]
    }
    return SESSION_CONFIG

class OpenAIRTHandler():
    incoming_websocket = None
    client = None
    connection = None
    connection_manager = None
    welcomed = False

    def __init__(self) -> None:
        print("Hello World")
        self.client = AsyncAzureOpenAI(
                azure_endpoint=AZURE_OPENAI_API_ENDPOINT,
                azure_deployment=AZURE_OPENAI_DEPLOYMENT_NAME,
                api_key=AZURE_OPENAI_API_KEY,
                api_version=AZURE_OPENAI_API_VERSION,
            )
        self.connection_manager = self.client.beta.realtime.connect(
                    model="gpt-4o-realtime-preview"
            )

    def __exit__(self, exc_type, exc_value, traceback):
        self.connection.close()
        self.incoming_websocket.close()

    
#init_websocket -> init_incoming_websocket (incoming)
    async def init_incoming_websocket(self, socket):
        # print("--inbound socket set")
        self.incoming_websocket = socket

#start_conversation > start_client
    async def start_client(self):
            self.connection = await self.connection_manager.enter()
            await self.connection.session.update(session=session_config())
            await self.connection.response.create()
            ### running an async task to listen and recieve oai messages
            asyncio.create_task(self.receive_oai_messages())


#receive_messages > receive_oai_messages
    async def receive_oai_messages(self):
        async for event in self.connection:
            if event is None:
                continue
            match event.type:
                case "session.created":
                    print("Session Created Message")
                    print(f"  Session Id: {event.session.id}")
                    pass
                case "error":
                    print(f"  Error: {event.error}")
                    pass
                case "input_audio_buffer.cleared":
                    print("Input Audio Buffer Cleared Message")
                    pass
                case "input_audio_buffer.speech_started":
                    print(f"Voice activity detection started at {event.audio_start_ms} [ms]")
                    await self.stop_audio()
                    pass
                case "input_audio_buffer.speech_stopped":
                    pass
                case "conversation.item.input_audio_transcription.completed":
                    print(f" User:-- {event.transcript}")
                case "conversation.item.input_audio_transcription.failed":
                    print(f"  Error: {event.error}")
                case "response.done":
                    print("Response Done Message")
                    print(f"  Response Id: {event.response.id}")
                    if event.response.status_details:
                        print(f"  Status Details: {event.response.status_details.model_dump_json()}")
                case "response.audio_transcript.done":
                    print(f" AI:-- {event.transcript}")
                case "response.audio.delta":
                    await self.oai_to_acs(event.delta)
                    pass
                case "response.function_call_arguments.done":
                    await self.handle_function_call(event)
                    pass
                case _:
                    pass

    
    async def handle_function_call(self, event):
        """Handle function call events from OpenAI"""
        try:
            function_name = event.name
            call_id = event.call_id
            
            print(f"Function call received: {function_name} with call_id: {call_id}")
            
            try:
                arguments = json.loads(event.arguments) if isinstance(event.arguments, str) else event.arguments
            except json.JSONDecodeError:
                print(f"Failed to parse arguments: {event.arguments}")
                arguments = {}
            
            if function_name == "get_ticket":
                result = await self.get_ticket_function(arguments)
                await self.send_function_call_result(result, call_id)
            elif function_name == "create_ticket":
                result = await self.create_ticket_function(arguments)
                await self.send_function_call_result(result, call_id)
            elif function_name == "end_call":
                result = await self.end_call_function(arguments)
                await self.send_function_call_result(result, call_id)
            else:
                error_result = f"Unknown function: {function_name}"
                await self.send_function_call_result(error_result, call_id)
                
        except Exception as e:
            print(f"Error handling function call: {e}")


    async def send_function_call_result(self, result, call_id):
        """Send function call result back to OpenAI"""
        try:
            await self.connection.conversation.item.create(
                item={
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": result
                }
            )
            
            await self.connection.response.create()
            print(f"Sent function call result: {result}")
            
        except Exception as e:
            print(f"Error sending function call result: {e}")
            

    async def send_welcome(self):
        if not self.welcomed:
            await self.connection.conversation.item.create(
            item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Hi! What's your name and who do you work for?"}],
                }
            )
            await self.connection.response.create()
            self.welcomed = True
            

# stop oai talking when detecting the user talking
    async def stop_audio(self):
            stop_audio_data = {
                "Kind": "StopAudio",
                "AudioData": None,
                "StopAudio": {}
            }

            json_data = json.dumps(stop_audio_data)
            await self.send_message(json_data)


    async def oai_to_acs(self, data):
        try:
            data = {
                "Kind": "AudioData",
                "AudioData": {
                        "Data":  data
                },
                "StopAudio": None
            }
            serialized_data = json.dumps(data)
            await self.send_message(serialized_data)
            
        except Exception as e:
            print(e)


    async def send_message(self, message: str):
        try:
            # FastAPI WebSocket expects text to be sent via send_text
            await self.incoming_websocket.send_text(message)
        except Exception as e:
            print(f"Failed to send message: {e}")


    async def acs_to_oai(self, stream_data):
        try:
            data = json.loads(stream_data)
            kind = data['kind']
            if kind == "AudioData":
                audio_data_section = data.get("audioData", {})
                if not audio_data_section.get("silent", True):
                    audio_data = audio_data_section.get("data")
                    await self.connection.input_audio_buffer.append(audio=audio_data)
        except Exception as e:
            print(f'Error processing WebSocket message: {e}')

# OpenAI Functions

    async def get_ticket_function(self, arguments):
        """Execute the get_ticket function"""

        url = f"{os.getenv("CALLBACK_URI_HOST")}/api/ticket/{arguments['ticket_id']}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url)

        message = response.text
        return message

    async def create_ticket_function(self, arguments):
        """Execute the create_ticket function"""
        url = f"{os.getenv("CALLBACK_URI_HOST")}/api/ticket"
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=arguments)

        message = response.text
        return message

    async def end_call_function(self, arguments):
        """Execute the end_call function"""
        try:
            url = f"{os.getenv("CALLBACK_URI_HOST")}/api/endCall"
            async with httpx.AsyncClient() as client:
                response = await client.post(url)

            if response.status_code == 200:
                return "Call ended successfully. Thank you for calling!"
            else:
                return "Failed to end call. Please try again."
        except Exception as e:
            print(f"Error ending call: {e}")
            return "Failed to end call due to an error."