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
        "instructions": "Your name is Mudasir, you work for Novizant Services. You're a helpful, calm and cheerful agent who responds with a clam British accent, but also can speak in any language or accent. Always start the conversation with a cheery hello, stating your name and who do you work for! You can also call functions when requested.",
        "voice": "shimmer",
        "modalities": ["text", "audio"],
        "tool_choice": "auto",
        "tools": [
            {
                "type": "function",
                "name": "health_check",
                "description": "Call the server to see if it is running",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "The name of the person requesting the health check"
                        }
                    },
                    "required": ["name"]
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
            
            if function_name == "health_check":
                result = await self.health_check_function(event.arguments)
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

    async def health_check_function(self, arguments):
        """Execute the health_check function"""

        url = "https://webapp-acs-oai-dev-westu2-001.azurewebsites.net/"
        async with httpx.AsyncClient() as client:
            response = await client.get(url)

        message = response.text
        return message
