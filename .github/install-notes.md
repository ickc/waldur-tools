This tag releases the whole repository — the package and the browser
extension, which are kept at the same version. The asset below is the
extension, for people who will never install the package.

**Chrome cannot install a downloaded zip directly.** Unpack it and load the
folder:

1. Download the `.zip` below and unpack it somewhere it can stay.
2. Open `chrome://extensions`, turn on **Developer mode**.
3. **Load unpacked**, and choose the unpacked folder — the one with
   `manifest.json` in it.

Then open your organisation's dashboard in the portal and press the toolbar
button. Nothing to configure: the token, the API URL and the organisation are
all read out of the tab you pressed it from.

Neither Python nor a checkout of this repository is needed — the plotly bundle
the report draws with is already in the archive.
