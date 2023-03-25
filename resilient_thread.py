import threading
import traceback
import time

retry_wait_time_secs = 30


class ResilientThread(threading.Thread):
    def __init__(self, discord_client, name, target=None, args=()):
        super(ResilientThread, self).__init__()
        self.stop_event = threading.Event()
        self.discord_client = discord_client
        self.name = name
        self.target = target
        self.args = args

    def run(self):
        while not self.stop_event.wait(30):
            try:
                if self.target:
                    self.target(*self.args)
            except Exception as e:
                message = f"Exception in ResilientThread {self.name}: {e}\n```{traceback.format_exc()}```"
                self.discord_client.send_error_msg(message)
                print(message)
                time.sleep(retry_wait_time_secs)

    def stop(self):
        self.stop_event.set()

    def restart(self):
        print(f"Restarting ResilientThread {self.name}...")
        self.stop()
        if self.is_alive():
            self.join()
        new_thread = ResilientThread(self.discord_client, self.name, self.target, self.args)
        new_thread.start()
