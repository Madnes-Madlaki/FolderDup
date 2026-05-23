import os
import sys
import hashlib
import threading
import tempfile
import base64
import atexit
import tkinter as tk
from tkinter import filedialog, scrolledtext, messagebox

# Try to use send2trash for Recycle Bin support (safer deletion)
try:
    import send2trash
    USE_RECYCLE_BIN = True
except ImportError:
    USE_RECYCLE_BIN = False
    # Fallback to permanent deletion (os.remove)

def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except AttributeError:
        # Running as script: use the directory of the script or current working directory
        base_path = os.path.dirname(sys.argv[0]) if os.path.dirname(sys.argv[0]) else os.path.abspath(".")
    return os.path.join(base_path, relative_path)

class DuplicateRemoverApp:
    def __init__(self, root):
        self.root = root
        self.root.title("FolderDup")
        self.root.geometry("700x500")
        self.root.resizable(True, True)

        # ---------- EMBEDDED ICON (Base64) ----------
        iconDataPath = resource_path("vencre.txt")
        if os.path.exists(iconDataPath):
            try:
                with open(iconDataPath, "r") as f:
                    b64_icon = f.read().strip()  # Read the whole Base64 string
                # Write the icon data to a temporary .ico file
                self.temp_icon = tempfile.NamedTemporaryFile(suffix=".ico", delete=False)
                self.temp_icon.write(base64.b64decode(b64_icon))
                self.temp_icon.close()
                self.root.iconbitmap(self.temp_icon.name)
                # Delete the temp file when the program exits
                atexit.register(lambda: os.unlink(self.temp_icon.name))
            except Exception as e:
                print(f"Could not set icon from file: {e}")
        # ---------------------------------------------

        # Folder selection
        self.folder_path = tk.StringVar()
        tk.Label(root, text="Select a folder to scan for duplicates:").pack(pady=5)
        frame = tk.Frame(root)
        frame.pack(pady=5)
        tk.Entry(frame, textvariable=self.folder_path, width=50).pack(side=tk.LEFT, padx=5)
        tk.Button(frame, text="Browse", command=self.browse_folder).pack(side=tk.LEFT)

        # Buttons
        self.scan_btn = tk.Button(root, text="Scan and Remove Duplicates", command=self.start_scan,
                                  state=tk.DISABLED)
        self.scan_btn.pack(pady=10)

        # Log area
        self.log = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=80, height=25)
        self.log.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

        # Warn if send2trash is missing
        if not USE_RECYCLE_BIN:
            self.log_insert("WARNING: send2trash not installed. Duplicates will be permanently deleted.\n"
                            "Install it with: pip install send2trash\n\n")

    def browse_folder(self):
        folder = filedialog.askdirectory(title="Select Folder to Scan")
        if folder:
            # Normalise the folder path to avoid Windows long‑path issues
            folder = os.path.normpath(os.path.abspath(folder))
            self.folder_path.set(folder)
            self.scan_btn.config(state=tk.NORMAL)
            self.log_insert(f"Selected folder: {folder}\n")

    def log_insert(self, text):
        """Thread-safe log insertion using 'after' method."""
        self.root.after(0, lambda: self.log.insert(tk.END, text))
        self.root.after(0, lambda: self.log.see(tk.END))

    def start_scan(self):
        folder = self.folder_path.get()
        if not os.path.isdir(folder):
            messagebox.showerror("Error", "Please select a valid folder.")
            return
        self.scan_btn.config(state=tk.DISABLED)
        self.log_insert("\n--- Starting scan ---\n")
        # Run the scanning in a separate thread
        thread = threading.Thread(target=self.scan_and_remove, args=(folder,), daemon=True)
        thread.start()

    def scan_and_remove(self, folder):
        """Main scanning and duplicate removal routine."""
        try:
            # Find duplicate groups (list of lists of normalised file paths)
            duplicates = self.find_duplicates(folder)
            if not duplicates:
                self.log_insert("No duplicate files found.\n")
                self.root.after(0, lambda: self.scan_btn.config(state=tk.NORMAL))
                return

            total_deleted = 0
            for group in duplicates:
                # Keep the first file (alphabetically by full path)
                keep_file = group[0]
                to_delete = group[1:]
                self.log_insert(f"\nDuplicate group (keeping '{os.path.basename(keep_file)}'):\n")
                for f in to_delete:
                    # Normalise path again for safety
                    f_norm = os.path.normpath(os.path.abspath(f))
                    self.log_insert(f"  Deleting: {f_norm}\n")
                    if not os.path.exists(f_norm):
                        self.log_insert(f"    SKIP: File does not exist.\n")
                        continue
                    try:
                        if USE_RECYCLE_BIN:
                            send2trash.send2trash(f_norm)
                        else:
                            os.remove(f_norm)
                        total_deleted += 1
                    except Exception as e:
                        self.log_insert(f"    ERROR: {e}\n")
            self.log_insert(f"\n--- Done ---\nDeleted {total_deleted} duplicate file(s).\n")
        except Exception as e:
            self.log_insert(f"Fatal error: {e}\n")
        finally:
            self.root.after(0, lambda: self.scan_btn.config(state=tk.NORMAL))

    def find_duplicates(self, folder):
        """
        Return a list of duplicate groups.
        Each group is a list of file paths (normalised) that are identical (content).
        """
        # Normalise the starting folder
        folder = os.path.normpath(os.path.abspath(folder))
        # Dictionary: size -> list of file paths
        size_map = {}
        # Walk through all files in the folder
        for root_dir, _, files in os.walk(folder):
            for file in files:
                full_path = os.path.normpath(os.path.join(root_dir, file))
                try:
                    size = os.path.getsize(full_path)
                    size_map.setdefault(size, []).append(full_path)
                except (OSError, PermissionError) as e:
                    self.log_insert(f"Skipping {full_path} (error: {e})\n")

        # For each size with more than one file, compute hash
        duplicates_by_hash = {}
        for size, file_list in size_map.items():
            if len(file_list) < 2:
                continue
            # Group by MD5 hash
            hash_map = {}
            for fpath in file_list:
                file_hash = self.compute_md5(fpath)
                if file_hash is None:
                    continue  # Skip unreadable files
                hash_map.setdefault(file_hash, []).append(fpath)
            # Keep only those hashes that appear more than once
            for hash_val, paths in hash_map.items():
                if len(paths) > 1:
                    # Sort paths to have a consistent "keep" order
                    duplicates_by_hash.setdefault(hash_val, []).extend(paths)

        # Convert to list of groups
        result = [sorted(paths) for paths in duplicates_by_hash.values()]
        return result

    def compute_md5(self, file_path, chunk_size=8192):
        """Compute MD5 hash of a file. Return None on error."""
        try:
            hash_md5 = hashlib.md5()
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(chunk_size), b""):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except (OSError, PermissionError):
            return None

if __name__ == "__main__":
    root = tk.Tk()
    app = DuplicateRemoverApp(root)
    root.mainloop()