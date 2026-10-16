# Vendored front-end libraries

Served by Caddy with the UI, so the UI and its tests need no CDN. Downloaded from the npm registry;
each tarball matched the registry's published sha512 integrity before extraction. Update by
repeating that check and changing the file names in `../index.html`.

| File | Package | License | sha256 |
| --- | --- | --- | --- |
| `cytoscape-3.34.3.min.js` | cytoscape@3.34.3 (`dist/cytoscape.min.js`) | MIT, `LICENSE.cytoscape` | `5f3b5b529546d5af1fc5628590af033b74511a5b6f789f5f4682845863228b91` |
| `alpinejs-3.17.4.min.js` | alpinejs@3.17.4 (`dist/cdn.min.js`) | MIT (declared in its package.json; the package ships no license file; https://github.com/alpinejs/alpine) | `232519394c6c8fdba6f362b1d9da16106db513cdbf899011f00daab4051df31c` |
