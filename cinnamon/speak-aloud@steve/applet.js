const Applet = imports.ui.applet;
const GLib = imports.gi.GLib;
const Util = imports.misc.util;
const PopupMenu = imports.ui.popupMenu;
const Mainloop = imports.mainloop;

class SpeakALoudApplet extends Applet.TextIconApplet {
    constructor(metadata, orientation, panel_height, instance_id) {
        super(orientation, panel_height, instance_id);
        this.metadata = metadata;
        this.set_applet_icon_name('audio-volume-high');
        this.set_applet_label('');
        this.set_applet_tooltip('Speak a Loud Universal TTS');

        this._script_dir = GLib.get_home_dir() + '/dev/tts/speak-aloud-linux';
        this._last_state = 'IDLE';

        this._build_menu();
        this._update_state();

        // Check status every 350ms
        this._timeout = Mainloop.timeout_add(350, () => {
            this._update_state();
            return true;
        });
    }

    _build_menu() {
        this.menuManager = new PopupMenu.PopupMenuManager(this);
        this.menu = new Applet.AppletPopupMenu(this, this.orientation);
        this.menuManager.addMenu(this.menu);

        let itemSpeak = new PopupMenu.PopupMenuItem('Speak Selection (Super+S)');
        itemSpeak.connect('activate', () => this._run_script('speak.sh'));
        this.menu.addMenuItem(itemSpeak);

        let itemPause = new PopupMenu.PopupMenuItem('Play / Pause');
        itemPause.connect('activate', () => this._run_script('speak-pause.sh'));
        this.menu.addMenuItem(itemPause);

        let itemStop = new PopupMenu.PopupMenuItem('Stop Playback');
        itemStop.connect('activate', () => this._run_script('speak-stop.sh'));
        this.menu.addMenuItem(itemStop);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        let itemQueue = new PopupMenu.PopupMenuItem('Open Settings Studio');
        itemQueue.connect('activate', () => this._run_script('tts-settings.sh'));
        this.menu.addMenuItem(itemQueue);
    }

    on_applet_clicked(event) {
        // Left click: toggle pause / play
        this._run_script('speak-pause.sh');
    }

    on_applet_middle_clicked(event) {
        // Middle click: stop playback
        this._run_script('speak-stop.sh');
    }

    _run_script(script, args = []) {
        let cmd = ['bash', this._script_dir + '/' + script, ...args];
        Util.spawnCommandLine(cmd.join(' '));
    }

    _update_state() {
        let state = 'IDLE';
        let paths = [
            GLib.get_user_runtime_dir() + '/speak-aloud-' + GLib.get_user_name() + '/status',
            '/run/user/' + GLib.get_user_name() + '/speak-aloud-' + GLib.get_user_name() + '/status',
            '/run/user/1000/speak-aloud-1000/status',
            '/tmp/speak-aloud-status',
        ];

        for (let p of paths) {
            if (GLib.file_test(p, GLib.FileTest.EXISTS)) {
                try {
                    let [ok, contents] = GLib.file_get_contents(p);
                    if (ok) {
                        state = (new TextDecoder().decode(contents)).trim();
                        break;
                    }
                } catch (e) {}
            }
        }

        if (state === this._last_state) return;
        this._last_state = state;

        if (state === 'PLAYING') {
            this.set_applet_icon_name('media-playback-start');
            this.set_applet_label('Playing');
            this.set_applet_tooltip('Speak a Loud — Playing (Click to Pause, Middle-click to Stop)');
        } else if (state === 'PAUSED') {
            this.set_applet_icon_name('media-playback-pause');
            this.set_applet_label('Paused');
            this.set_applet_tooltip('Speak a Loud — Paused (Click to Resume)');
        } else if (state === 'GENERATING') {
            this.set_applet_icon_name('process-working');
            this.set_applet_label('Generating…');
            this.set_applet_tooltip('Speak a Loud — Generating Audio…');
        } else if (state === 'RETRYING') {
            this.set_applet_icon_name('view-refresh');
            this.set_applet_label('Retrying…');
            this.set_applet_tooltip('Speak a Loud — Retrying Network Connection');
        } else {
            this.set_applet_icon_name('audio-volume-high');
            this.set_applet_label('');
            this.set_applet_tooltip('Speak a Loud — ○ Ready (Super+S to Speak)');
        }
    }

    on_applet_removed_from_panel() {
        if (this._timeout) {
            Mainloop.source_remove(this._timeout);
            this._timeout = null;
        }
    }
}

function main(metadata, orientation, panel_height, instance_id) {
    return new SpeakALoudApplet(metadata, orientation, panel_height, instance_id);
}
