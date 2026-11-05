import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from unittest.mock import patch

try:
    import tkinter as tk
except ImportError:
    tk = None


@unittest.skipIf(tk is None, 'Tk is unavailable')
class AppWorkflowTests(unittest.TestCase):
    def test_scan_filter_and_download_to_chosen_folder(self):
        from app import App

        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f'Tk display is unavailable: {error}')
        self.addCleanup(root.destroy)
        root.withdraw()
        app = App(root)
        url = 'https://www.facebook.com/example'
        rows = [
            dict(kind='image', facebook_url=f'{url}/photos/101', media_url='', alt='', post_date='2026-03-31'),
            dict(kind='video', facebook_url=f'{url}/videos/102', media_url='', alt='', post_date='2026-04-01'),
            dict(kind='video', facebook_url=f'{url}/videos/103', media_url='', alt='', post_date=''),
        ]

        def wait_for_result():
            deadline = monotonic() + 3
            while app.busy and monotonic() < deadline:
                root.update()
                sleep(0.01)
            self.assertFalse(app.busy, 'Background operation did not finish')

        with (
            TemporaryDirectory() as folder,
            patch('app.scan', return_value=(rows, {})) as scan,
            patch('app.filedialog.askdirectory', return_value=folder) as choose_directory,
            patch('app.save', return_value=[{
                **rows[1], 'file': '2026-04/video.mp4', 'download_status': 'downloaded',
            }]) as save,
            patch('tkinter.messagebox.showerror') as show_error,
            patch('tkinter.messagebox.showwarning'),
            patch('tkinter.messagebox.showinfo'),
        ):
            app.url.set(url)
            app.start_scan()
            wait_for_result()
            self.assertEqual(scan.call_args.args[0], url)
            self.assertEqual(len(app.rows), 3)

            app.date_from.set('2026-04-01')
            app.date_to.set('2026-04-01')
            app.refresh_results()
            self.assertEqual([app.rows[index] for index in app.matching], [rows[1]])

            app.browse_destination()
            choose_directory.assert_called_once()
            app.start_download()
            wait_for_result()
            save.assert_called_once()
            selected_rows, destination = save.call_args.args
            self.assertEqual(selected_rows, [rows[1]])
            self.assertEqual(Path(destination), Path(folder))
            show_error.assert_not_called()
