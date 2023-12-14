import enum
import time
import threading
import argparse
import platform
import pyaudio
import numpy as np
from faster_whisper import WhisperModel
from pynput import keyboard
from transitions import Machine



if platform.system() == 'Windows':
    import winsound
    def playsound(s, wait=True):
        # SND_ASYNC winsound cannot play asynchronously from memory
        winsound.PlaySound(s, winsound.SND_MEMORY)
    def loadwav(filename):
        with open(filename, "rb") as f:
            data = f.read()
        return data            
else:
    import soundfile as sf
    import sounddevice # or pygame.mixer, py-simple-audio
    sounddevice.default.samplerate = 44100
    def playsound(s, wait=True):
        sounddevice.play(s) # samplerate=16000
        if wait:
            sounddevice.wait()
    def loadwav(filename):
        data, fs = sf.read(filename, dtype='float32')
        return data            


class SpeechTranscriber:
    def __init__(self, callback, model_size='base', device='cpu', compute_type="int8"):
        self.callback = callback
        self.model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def transcribe(self, event):
        print('Transcribing...')
        audio = event.kwargs.get('audio', None)
        if audio is not None:
            segments, info = self.model.transcribe(audio, beam_size=5)
            print("Detected language '%s' with probability %f" % (info.language, info.language_probability))
            self.callback(segments=segments)
        else:
            self.callback(segments=[])

class Recorder:
    def __init__(self, callback):
        self.callback = callback
        self.recording = False

    def start(self, language=None):
        print('Recording ...')
        thread = threading.Thread(target=self._record_impl, args=())
        thread.start()

    def stop(self):
        print('Done recording.')
        self.recording = False

    def _record_impl(self):
        self.recording = True

        frames_per_buffer = 1024
        p = pyaudio.PyAudio()
        stream = p.open(format            = pyaudio.paInt16,
                        channels          = 1,
                        rate              = 16000,
                        frames_per_buffer = frames_per_buffer,
                        input             = True)
        frames = []

        while self.recording:
            data = stream.read(frames_per_buffer)
            frames.append(data)

        stream.stop_stream()
        stream.close()
        p.terminate()

        audio_data = np.frombuffer(b''.join(frames), dtype=np.int16)
        audio_data_fp32 = audio_data.astype(np.float32) / 32768.0

        self.callback(audio=audio_data_fp32)


class KeyboardReplayer():
    def __init__(self, callback):
        self.callback = callback
        self.kb = keyboard.Controller()
    def replay(self, event):
        print('Typing transcribed words...')
        segments = event.kwargs.get('segments', [])
        for segment in segments:
            is_first = True
            for element in segment.text:
                if is_first and element == " ":
                    is_first = False
                    continue
                try:
                    print(element, end='')
                    self.kb.type(element)
                    time.sleep(0.0025)
                except:
                    pass
        print('')
        self.callback()


class KeyListener():
    def __init__(self, callback, key):
        self.callback = callback
        self.key = key
    def run(self):
        with keyboard.GlobalHotKeys({self.key : self.callback}) as h:
            h.join()


class DoubleKeyListener():
    def __init__(self, activate_callback, deactivate_callback, key=keyboard.Key.cmd_r):
        self.activate_callback = activate_callback
        self.deactivate_callback = deactivate_callback
        self.key = key
        self.pressed = 0
        self.last_press_time = 0

    def on_press(self, key):
        if key == self.key:
            current_time = time.time()
            is_dbl_click = current_time - self.last_press_time < 0.5
            self.last_press_time = current_time
            if is_dbl_click:
                return self.activate_callback()
            else:
                return self.deactivate_callback()

    def on_release(self, key):
        pass
    def run(self):
        with keyboard.Listener(
                on_press=self.on_press,
                on_release=self.on_release) as listener:
            listener.join()


def parse_args():
    parser = argparse.ArgumentParser(description='Dictation app powered by Faster whisper')
    parser.add_argument('-m', '--model-name', type=str, default='base',
                        help='''\
Size of the model to use
(tiny, tiny.en, base, base.en, small, small.en, medium, medium.en, large-v1, large-v2, or large).
A path to a converted model directory, or a CTranslate2-converted Whisper model ID from the Hugging Face Hub.
When a size or a model ID is configured, the converted model is downloaded from the Hugging Face Hub.
Default: base.''')
    parser.add_argument('-k', '--key-combo', type=str,
                        help='''\
Specify the key combination to toggle the app.

See https://pynput.readthedocs.io/en/latest/keyboard.html#pynput.keyboard.Key for a list of keys supported.

Examples: <cmd_l>+<alt>+x , <ctrl>+<alt>+a. Note on windows, the winkey is specified using <cmd>.

Default: <win>+z on Windows (see below for MacOS and Linux defaults).''')
    parser.add_argument('-d', '--double-key', type=str,
                        help='''\
If key-combo is not set, on macOS/linux the default behavior is double tapping a key to start recording.
Tap the same key again to stop recording.

On MacOS the key is Right Cmd and on Linux the key is Right Super (Right Win Key)

You can set to a different key for double triggering.

''')
    parser.add_argument('-t', '--max-time', type=int, default=30,
                        help='''\
Specify the maximum recording time in seconds.
The app will automatically stop recording after this duration.
Default: 30 seconds.''')
    parser.add_argument('-v', '--device', type=str, default='cpu',
                        help='''\
By default we use 'cpu' for inference.
If you have supported GPU with proper driver and libraries installed, you can set it to 'auto' or 'cuda'.''')

    parser.add_argument('-c', '--compute-type', type=str, default='int8',
                        help='''\
If your GPU stack supports it, you can set compute-type to 'float32' or 'float16' to improve accuracy. Default 'int8' ''')

    args = parser.parse_args()
    return args


class States(enum.Enum):
    READY        = 1
    RECORDING    = 2
    TRANSCRIBING = 3
    REPLAYING    = 4


transitions = [
    {'trigger':'start_recording'     ,'source': States.READY        ,'dest': States.RECORDING    },
    {'trigger':'finish_recording'    ,'source': States.RECORDING    ,'dest': States.TRANSCRIBING },
    {'trigger':'finish_transcribing' ,'source': States.TRANSCRIBING ,'dest': States.REPLAYING    },
    {'trigger':'finish_replaying'    ,'source': States.REPLAYING    ,'dest': States.READY        },
]


class App():
    def __init__(self, args):
        m = Machine(states=States, transitions=transitions, send_event=True, ignore_invalid_triggers=True, initial=States.READY)

        self.m = m
        self.args = args
        self.recorder    = Recorder(m.finish_recording)
        self.transcriber = SpeechTranscriber(m.finish_transcribing, args.model_name, args.device, args.compute_type)
        self.replayer    = KeyboardReplayer(m.finish_replaying)
        self.timer = None

        m.on_enter_RECORDING(self.recorder.start)
        m.on_enter_TRANSCRIBING(self.transcriber.transcribe)
        m.on_enter_REPLAYING(self.replayer.replay)

        # https://freesound.org/people/leviclaassen/sounds/107786/
        # https://freesound.org/people/MATRIXXX_/
        self.SOUND_EFFECTS = {
            "start_recording": loadwav("assets/granted-04.wav"),
            "finish_recording": loadwav("assets/beepbeep.wav")
        }

    def beep(self, k, wait=True):
        # wait=True will block until the beeping sound finished playing before continue to start recording
        # just in case if the beep sound interfere with voice recording
        # when done recording, we don't want to block while continuing to transcribe while beeping async
        playsound(self.SOUND_EFFECTS[k], wait=wait)

    def start(self):
        if self.m.is_READY():
            self.beep("start_recording")
            if self.args.max_time:
                self.timer = threading.Timer(self.args.max_time, self.timer_stop)
                self.timer.start()
            self.m.start_recording()
            return True

    def stop(self):
        if self.m.is_RECORDING():
            self.recorder.stop()
            if self.timer is not None:
                self.timer.cancel()
            self.beep("finish_recording", wait=False)
            return True

    def timer_stop(self):
        print('Timer stop')
        self.stop()

    def toggle(self):
        return self.start() or self.stop()

    def run(self):
        def normalize_key_names(keyseqs, parse=False):
            k = keyseqs.replace('<win>', '<cmd>').replace('<win_r>', '<cmd_r>').replace('<win_l>', '<cmd_l>').replace('<super>', '<cmd>').replace('<super_r>', '<cmd_r>').replace('<super_l>', '<cmd_l>')
            if parse:
                k = keyboard.HotKey.parse(k)[0]
            print('Using key:', k)
            return k

        if (platform.system() != 'Windows' and not self.args.key_combo) or self.args.double_key:
            key = self.args.double_key or (platform.system() == 'Linux' and '<super_r>') or '<cmd_r>'
            keylistener= DoubleKeyListener(self.start, self.stop, normalize_key_names(key, parse=True))
            self.m.on_enter_READY(lambda *_: print("Double tap ", key, " to start recording. Tap again to stop recording"))
        else:
            key = self.args.key_combo or '<win>+z'
            keylistener= KeyListener(self.toggle, normalize_key_names(key))
            self.m.on_enter_READY(lambda *_: print("Press ", key, " to start/stop recording."))
        self.m.to_READY()
        keylistener.run()


if __name__ == "__main__":
    args = parse_args()
    App(args).run()
