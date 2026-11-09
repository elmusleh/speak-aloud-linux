const Desklet = imports.ui.desklet;
const St = imports.gi.St;
const GLib = imports.gi.GLib;
const Util = imports.misc.util;
const Mainloop = imports.mainloop;

class SpeakALoudDesklet extends Desklet.Desklet {
    constructor(metadata, desklet_id) {
        super(metadata, desklet_id);
        this.metadata = metadata;
        this._script_dir = GLib.get_home_dir() + '/dev/tts/speak-aloud-linux';

        // Root container widget
        this._container = new St.BoxLayout({
            vertical: true,
            style: 'background-color: rgba(18, 20, 26, 0.88); border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 12px; padding: 14px; min-width: 280px;'
        });

        // Header row
        let headerRow = new St.BoxLayout({ vertical: false, style: 'margin-bottom: 8px;' });
        this._titleLabel = new St.Label({
            text: 'Speak a Loud',
            style: 'font-weight: 700; font-size: 13px; color: #818CF8;'
        });
        this._statusLabel = new St.Label({
            text: '○ Ready',
            style: 'font-size: 11px; color: #9CA3AF; margin-left: 8px;'
        });
        headerRow.add_actor(this._titleLabel);
        headerRow.add_actor(this._statusLabel);
        this._container.add_actor(headerRow);

        // Current Quote / Sentence Preview
        this._quoteLabel = new St.Label({
            text: 'No active playback.',
            style: 'font-size: 11px; color: #E5E7EB; margin-bottom: 10px; max-width: 260px;'
        });
        this._quoteLabel.clutter_text.set_line_wrap(true);
        this._container.add_actor(this._quoteLabel);

        // Progress bar label
        this._progressLabel = new St.Label({
            text: '00:00 / 00:00 (0%)',
            style: 'font-size: 10px; color: #6B7280; margin-bottom: 10px; font-family: monospace;'
        });
        this._container.add_actor(this._progressLabel);

        // Button controls row (⏮, ⏯, ⏭, ⏹)
        let btnRow = new St.BoxLayout({ vertical: false, style: 'spacing: 8px;' });

        let btnRewind = new St.Button({
            label: '⏮ -10s',
            style: 'background-color: rgba(255, 255, 255, 0.08); padding: 4px 8px; border-radius: 6px; font-size: 11px; color: #F3F4F6;'
        });
        btnRewind.connect('clicked', () => this._run_script('speak-hud.sh', ['--seek-back']));
        btnRow.add_actor(btnRewind);

        this._btnPlayPause = new St.Button({
            label: '⏯ Play/Pause',
            style: 'background-color: #6366F1; padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 600; color: #FFFFFF;'
        });
        this._btnPlayPause.connect('clicked', () => this._run_script('speak-pause.sh'));
        btnRow.add_actor(this._btnPlayPause);

        let btnForward = new St.Button({
            label: '+10s ⏭',
            style: 'background-color: rgba(255, 255, 255, 0.08); padding: 4px 8px; border-radius: 6px; font-size: 11px; color: #F3F4F6;'
        });
        btnForward.connect('clicked', () => this._run_script('speak-hud.sh', ['--seek-forward']));
        btnRow.add_actor(btnForward);

        let btnStop = new St.Button({
            label: '⏹ Stop',
            style: 'background-color: rgba(239, 68, 68, 0.2); border: 1px solid rgba(239, 68, 68, 0.4); padding: 4px 8px; border-radius: 6px; font-size: 11px; color: #FCA5A5;'
        });
        btnStop.connect('clicked', () => this._run_script('speak-stop.sh'));
        btnRow.add_actor(btnStop);

        this._container.add_actor(btnRow);
        this.setContent(this._container);
        this.setHeader(_("Speak a Loud"));

        this._updateState();
        this._timerId = Mainloop.timeout_add(400, () => {
            this._updateState();
            return true;
        });
    }

    _run_script(script, args = []) {
        let cmd = ['bash', this._script_dir + '/' + script, ...args];
        Util.spawnCommandLine(cmd.join(' '));
    }

    _updateState() {
        let runtimeDir = GLib.get_user_runtime_dir() + '/speak-aloud-' + GLib.get_user_name();
        let statusPath = runtimeDir + '/status';
        let sentencePath = runtimeDir + '/current_sentence';

        let state = 'IDLE';
        try {
            if (GLib.file_test(statusPath, GLib.FileTest.EXISTS)) {
                let [ok, contents] = GLib.file_get_contents(statusPath);
                if (ok) state = ('' + contents).trim();
            }
        } catch (e) {}

        let quote = '';
        try {
            if (GLib.file_test(sentencePath, GLib.FileTest.EXISTS)) {
                let [ok, contents] = GLib.file_get_contents(sentencePath);
                if (ok) quote = ('' + contents).trim();
            }
        } catch (e) {}

        if (state === 'PLAYING') {
            this._statusLabel.text = '● Speaking';
            this._statusLabel.style = 'font-size: 11px; color: #34D399; margin-left: 8px; font-weight: 600;';
        } else if (state === 'PAUSED') {
            this._statusLabel.text = '❚❚ Paused';
            this._statusLabel.style = 'font-size: 11px; color: #FBBF24; margin-left: 8px; font-weight: 600;';
        } else if (state === 'GENERATING') {
            this._statusLabel.text = '◌ Generating...';
            this._statusLabel.style = 'font-size: 11px; color: #60A5FA; margin-left: 8px;';
        } else {
            this._statusLabel.text = '○ Ready';
            this._statusLabel.style = 'font-size: 11px; color: #9CA3AF; margin-left: 8px;';
        }

        if (quote) {
            this._quoteLabel.text = quote.length > 95 ? quote.substring(0, 92) + '...' : quote;
        } else {
            this._quoteLabel.text = state === 'IDLE' ? 'Ready to read. Press Super+S to speak.' : 'Speaking passage...';
        }
    }

    on_desklet_removed() {
        if (this._timerId) {
            Mainloop.source_remove(this._timerId);
            this._timerId = 0;
        }
    }
}

function main(metadata, desklet_id) {
    return new SpeakALoudDesklet(metadata, desklet_id);
}
