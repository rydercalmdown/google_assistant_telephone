import concurrent.futures
import json
import logging
import os
import os.path
import pathlib2 as pathlib
import sys
import time
import uuid

import grpc
import google.auth.transport.grpc
import google.auth.transport.requests
import google.oauth2.credentials

import assistant_helpers
import audio_helpers
import device_helpers
from tenacity import retry, stop_after_attempt, retry_if_exception
from google.assistant.embedded.v1alpha2 import (
    embedded_assistant_pb2,
    embedded_assistant_pb2_grpc
)


class GoogleAssistant(object):
    """Google Assistant Object"""

    def __init__(self):
        root_dir = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        self.language_code = 'en-US'
        self.device_config_path = os.path.join(root_dir, 'device_config.json')
        self.device_credentials_path = os.path.join(root_dir, 'credentials.json')
        self._set_credentials()
        self._load_device_config()
        self._create_conversation_stream()
        self.display = False
        self._set_http_request()
        self._create_gprc_channel()
        self.conversation_state = None
        self.is_new_conversation = True
        self.assistant = embedded_assistant_pb2_grpc.EmbeddedAssistantStub(
            self.channel
        )
        self.deadline = 60 * 3 + 5
        self.device_handler = device_helpers.DeviceRequestHandler(self.device_id)

    def _get_audio_source(self):
        """Returns the system audio souruce"""
        return audio_helpers.SoundDeviceStream(
            sample_rate=audio_helpers.DEFAULT_AUDIO_SAMPLE_RATE,
            sample_width=audio_helpers.DEFAULT_AUDIO_SAMPLE_WIDTH,
            block_size=audio_helpers.DEFAULT_AUDIO_DEVICE_BLOCK_SIZE,
            flush_size=audio_helpers.DEFAULT_AUDIO_DEVICE_FLUSH_SIZE
        )

    def _get_audio_sink(self):
        """Returns the system audio sink"""
        return self._get_audio_source()

    def _create_conversation_stream(self):
        """Create conversation stream"""
        self.conversation_stream = audio_helpers.ConversationStream(
            source=self._get_audio_source(),
            sink=self._get_audio_sink(),
            iter_size=audio_helpers.DEFAULT_AUDIO_ITER_SIZE,
            sample_width=audio_helpers.DEFAULT_AUDIO_SAMPLE_WIDTH,
        )

    def _load_device_config(self):
        """Load device config from file"""
        try:
            with open(self.device_config_path) as f:
                device = json.load(f)
                self.device_id = device['id']
                self.device_model_id = device['model_id']
        except Exception:
            self._register_device_config()

    def _register_device_config(self):
        """Registers device config to file if not found"""
        self.project_id = os.environ['PROJECT_ID']
        self.device_model_id = os.environ['DEVICE_MODEL_ID']
        endpoint = 'embeddedassistant.googleapis.com'
        device_base_url = (
            'https://%s/v1alpha2/projects/%s/devices' % (endpoint,
                                                            self.project_id)
        )
        self.device_id = str(uuid.uuid1())
        payload = {
            'id': self.device_id,
            'model_id': self.device_model_id,
            'client_type': 'SDK_SERVICE'
        }
        session = google.auth.transport.requests.AuthorizedSession(
            self.credentials
        )
        r = session.post(device_base_url, data=json.dumps(payload))
        if r.status_code != 200:
            logging.error('Failed to register device: %s', r.text)
            sys.exit(-1)
        with open(self.device_config_path, 'w') as f:
            json.dump(payload, f)

    def _create_gprc_channel(self):
        """Create a gRPC channel"""
        endpoint = 'embeddedassistant.googleapis.com'
        self.channel = google.auth.transport.grpc.secure_authorized_channel(
            self.credentials, self.http_request, endpoint
        )

    def _set_http_request(self):
        """Sets the Request object"""
        self.http_request = google.auth.transport.requests.Request()
        self.credentials.refresh(self.http_request)

    def _set_credentials(self):
        """Set credentials from file"""
        with open(self.device_credentials_path, 'r') as f:
            self.credentials = google.oauth2.credentials.Credentials(
                token=None, **json.load(f))

    def __enter__(self):
        return self

    def __exit__(self, etype, e, traceback):
        if e:
            return False
        self.conversation_stream.close()

    def is_grpc_error_unavailable(e):
        is_grpc_error = isinstance(e, grpc.RpcError)
        if is_grpc_error and (e.code() == grpc.StatusCode.UNAVAILABLE):
            logging.error('grpc unavailable error: %s', e)
            return True
        return False

    @retry(reraise=True, stop=stop_after_attempt(3),
           retry=retry_if_exception(is_grpc_error_unavailable))
    def assist(self):
        """Send a voice request to the Assistant and playback the response.
        Returns: True if conversation should continue.
        """
        continue_conversation = False
        device_actions_futures = []

        self.conversation_stream.start_recording()
        logging.info('Recording audio request.')

        def iter_log_assist_requests():
            for c in self.gen_assist_requests():
                assistant_helpers.log_assist_request_without_audio(c)
                yield c
            logging.debug('Reached end of AssistRequest iteration.')

        # This generator yields AssistResponse proto messages
        # received from the gRPC Google Assistant API.
        for resp in self.assistant.Assist(iter_log_assist_requests(),
                                          self.deadline):
            assistant_helpers.log_assist_response_without_audio(resp)
            if resp.event_type == embedded_assistant_pb2.AssistResponse.END_OF_UTTERANCE:
                logging.info('End of audio request detected.')
                logging.info('Stopping recording.')
                self.conversation_stream.stop_recording()
            if resp.speech_results:
                logging.info('Transcript of user request: "%s".',
                             ' '.join(r.transcript
                                      for r in resp.speech_results))
            if len(resp.audio_out.audio_data) > 0:
                if not self.conversation_stream.playing:
                    self.conversation_stream.stop_recording()
                    self.conversation_stream.start_playback()
                    logging.info('Playing assistant response.')
                self.conversation_stream.write(resp.audio_out.audio_data)
            if resp.dialog_state_out.conversation_state:
                conversation_state = resp.dialog_state_out.conversation_state
                logging.debug('Updating conversation state.')
                self.conversation_state = conversation_state
            if resp.dialog_state_out.volume_percentage != 0:
                volume_percentage = resp.dialog_state_out.volume_percentage
                logging.info('Setting volume to %s%%', volume_percentage)
                self.conversation_stream.volume_percentage = volume_percentage
            if resp.dialog_state_out.microphone_mode == embedded_assistant_pb2.DialogStateOut.DIALOG_FOLLOW_ON:
                continue_conversation = True
                logging.info('Expecting follow-on query from user.')
            elif resp.dialog_state_out.microphone_mode == embedded_assistant_pb2.DialogStateOut.CLOSE_MICROPHONE:
                continue_conversation = False
            if resp.device_action.device_request_json:
                device_request = json.loads(
                    resp.device_action.device_request_json
                )
                fs = self.device_handler(device_request)
                if fs:
                    device_actions_futures.extend(fs)

        if len(device_actions_futures):
            logging.info('Waiting for device executions to complete.')
            concurrent.futures.wait(device_actions_futures)

        logging.info('Finished playing assistant response.')
        self.conversation_stream.stop_playback()
        return continue_conversation

    def gen_assist_requests(self):
        """Yields: AssistRequest messages to send to the API."""

        config = embedded_assistant_pb2.AssistConfig(
            audio_in_config=embedded_assistant_pb2.AudioInConfig(
                encoding='LINEAR16',
                sample_rate_hertz=self.conversation_stream.sample_rate,
            ),
            audio_out_config=embedded_assistant_pb2.AudioOutConfig(
                encoding='LINEAR16',
                sample_rate_hertz=self.conversation_stream.sample_rate,
                volume_percentage=self.conversation_stream.volume_percentage,
            ),
            dialog_state_in=embedded_assistant_pb2.DialogStateIn(
                language_code=self.language_code,
                conversation_state=self.conversation_state,
                is_new_conversation=self.is_new_conversation,
            ),
            device_config=embedded_assistant_pb2.DeviceConfig(
                device_id=self.device_id,
                device_model_id=self.device_model_id,
            )
        )
        if self.display:
            config.screen_out_config.screen_mode = embedded_assistant_pb2.ScreenOutConfig.PLAYING
        self.is_new_conversation = False
        yield embedded_assistant_pb2.AssistRequest(config=config)
        for data in self.conversation_stream:
            yield embedded_assistant_pb2.AssistRequest(audio_in=data)
