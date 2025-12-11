#!/usr/bin/env python3
import sys
import time
import traceback

import dbus
from dbus.mainloop.glib import DBusGMainLoop

import gi

# Explicitly use GTK 3 so Gtk.main() is available
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # type: ignore

# Route dbus callbacks into the GLib/GTK main loop
DBusGMainLoop(set_as_default=True)


def log(msg: str) -> None:
    print(f"[popup] {msg}", flush=True)


class CallWindow(Gtk.Window):
    """Tiny popup window for a single call."""

    def __init__(self, caller_id: str, on_answer, on_hangup):
        super().__init__(title="Phone Call")
        self.set_keep_above(True)
        self.set_resizable(False)
        self.set_border_width(12)
        self.set_position(Gtk.WindowPosition.CENTER_ALWAYS)

        caller_text = caller_id or "Unknown"
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add(vbox)

        self.state_label = Gtk.Label(label="Incoming call")
        self.state_label.set_xalign(0)
        vbox.pack_start(self.state_label, False, False, 0)

        self.caller_label = Gtk.Label(label=f"From: {caller_text}")
        self.caller_label.set_xalign(0)
        vbox.pack_start(self.caller_label, False, False, 0)

        self.timer_label = Gtk.Label(label="00:00")
        self.timer_label.set_xalign(0)
        vbox.pack_start(self.timer_label, False, False, 0)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        vbox.pack_start(button_box, False, False, 0)

        self.answer_btn = Gtk.Button(label="Answer")
        self.answer_btn.connect("clicked", lambda *_: on_answer())
        button_box.pack_start(self.answer_btn, True, True, 0)

        self.hangup_btn = Gtk.Button(label="Hang up")
        self.hangup_btn.connect("clicked", lambda *_: on_hangup())
        button_box.pack_start(self.hangup_btn, True, True, 0)

    def show_incoming(self) -> None:
        self.state_label.set_text("Incoming call")
        self.answer_btn.set_sensitive(True)
        self.hangup_btn.set_sensitive(True)
        self.timer_label.set_text("Ringing…")
        self.present()

    def show_active(self) -> None:
        self.state_label.set_text("Call in progress")
        self.answer_btn.set_sensitive(False)
        self.hangup_btn.set_sensitive(True)
        self.present()

    def update_timer_label(self, seconds: int) -> None:
        mins, secs = divmod(max(seconds, 0), 60)
        self.timer_label.set_text(f"{mins:02d}:{secs:02d}")


class PopupApp:
    def __init__(self):
        self.bus = dbus.SessionBus()
        self.calls = {}  # call_path -> data dict
        self._subscribe()

    # -------------------- DBus wiring -------------------- #
    def _subscribe(self) -> None:
        log("Subscribing to PipeWire telephony signals")
        try:
            self.bus.add_signal_receiver(
                self.on_interfaces_added,
                dbus_interface="org.freedesktop.DBus.ObjectManager",
                signal_name="InterfacesAdded",
                bus_name="org.pipewire.Telephony",
                path="/org/pipewire/Telephony/ag1",
            )
            self.bus.add_signal_receiver(
                self.on_interfaces_removed,
                dbus_interface="org.freedesktop.DBus.ObjectManager",
                signal_name="InterfacesRemoved",
                bus_name="org.pipewire.Telephony",
                path="/org/pipewire/Telephony/ag1",
            )
            self.bus.add_signal_receiver(
                self.on_properties_changed,
                dbus_interface="org.freedesktop.DBus.Properties",
                signal_name="PropertiesChanged",
                bus_name="org.pipewire.Telephony",
                path_keyword="path",
            )
        except Exception as exc:  # pragma: no cover - runtime wiring
            log(f"Failed to subscribe: {exc}")
            traceback.print_exc()
            sys.exit(1)

    # -------------------- Signal handlers -------------------- #
    def on_interfaces_added(self, path, interfaces, **_kwargs):
        """New call appeared."""
        call_props = interfaces.get("org.pipewire.Telephony.Call1")
        if not call_props:
            return

        state = call_props.get("State", "")
        caller_id = call_props.get("LineIdentification", "Unknown")
        log(f"Incoming call: {caller_id} (state={state}) path={path}")

        if state == "incoming":
            self._show_window(path, caller_id, initial_state="incoming")

    def on_interfaces_removed(self, path, interfaces, **_kwargs):
        """Call removed (ended)."""
        if "org.pipewire.Telephony.Call1" not in interfaces:
            return
        log(f"Call removed: {path}")
        self._close_call(path)

    def on_properties_changed(self, interface, changed_props, _invalidated, **kwargs):
        if interface != "org.pipewire.Telephony.Call1":
            return

        state = changed_props.get("State")
        if not state:
            return

        call_path = kwargs.get("path")
        if not call_path:
            return
        log(f"State changed: {call_path} -> {state}")

        if state == "active":
            self._mark_active(call_path)
        elif state == "disconnected":
            self._close_call(call_path)

    # -------------------- UI helpers -------------------- #
    def _show_window(self, call_path: str, caller_id: str, initial_state: str) -> None:
        if call_path in self.calls:
            self.calls[call_path]["window"].present()
            return

        window = CallWindow(
            caller_id,
            on_answer=lambda: self.answer_call(call_path),
            on_hangup=lambda: self.hangup_call(call_path),
        )
        window.connect("delete-event", lambda *_: True)  # keep window persistent
        window.connect("destroy", lambda *_: self._close_call(call_path))
        window.show_all()

        self.calls[call_path] = {
            "caller_id": caller_id or "Unknown",
            "state": initial_state,
            "window": window,
            "start_time": None,
            "timer_id": None,
        }

        if initial_state == "active":
            self._mark_active(call_path)
        else:
            window.show_incoming()

    def _mark_active(self, call_path: str) -> None:
        call = self.calls.get(call_path)
        if not call:
            return

        call["state"] = "active"
        call["start_time"] = time.monotonic()
        call["window"].show_active()

        if call["timer_id"]:
            GLib.source_remove(call["timer_id"])

        # Start 1-second timer to update label
        call["timer_id"] = GLib.timeout_add_seconds(
            1, lambda: self._update_timer(call_path)
        )
        self._update_timer(call_path)

    def _update_timer(self, call_path: str) -> bool:
        call = self.calls.get(call_path)
        if not call or call["state"] != "active" or call["start_time"] is None:
            return False

        elapsed = int(time.monotonic() - call["start_time"])
        call["window"].update_timer_label(elapsed)
        return True  # keep the timer running

    def _close_call(self, call_path: str) -> None:
        call = self.calls.pop(call_path, None)
        if not call:
            return

        if call["timer_id"]:
            GLib.source_remove(call["timer_id"])
        try:
            call["window"].destroy()
        except Exception:
            pass
        log(f"Closed call UI for {call_path}")

    # -------------------- Call control -------------------- #
    def _get_call_iface(self, call_path: str):
        call_obj = self.bus.get_object("org.pipewire.Telephony", call_path)
        return dbus.Interface(call_obj, "org.pipewire.Telephony.Call1")

    def answer_call(self, call_path: str) -> None:
        log(f"Answering {call_path}")
        try:
            self._get_call_iface(call_path).Answer()
        except Exception as exc:
            log(f"Answer failed: {exc}")
            traceback.print_exc()

    def hangup_call(self, call_path: str) -> None:
        log(f"Hanging up {call_path}")
        try:
            self._get_call_iface(call_path).Hangup()
        except Exception as exc:
            log(f"Hangup failed: {exc}")
            traceback.print_exc()


def main() -> None:
    app = PopupApp()
    log("Ready for calls (GTK popup)")
    try:
        Gtk.main()
    except KeyboardInterrupt:
        log("Exiting...")
        sys.exit(0)


if __name__ == "__main__":
    main()

