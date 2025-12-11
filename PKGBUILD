pkgname=gtk-phone-popup
pkgver=0.1.0
pkgrel=1
pkgdesc="GTK popup for PipeWire telephony calls with answer/hangup"
arch=('any')
license=('custom:MIT')  # adjust if different
depends=('python' 'python-dbus' 'python-gobject' 'pipewire')
source=("git+https://github.com/arcceus/gtk-phone-popup-pkg.git"
        "gtk-popup.service")
sha256sums=('SKIP' 'SKIP')

package() {
  install -Dm755 "$srcdir/gtk_popup.py" \
    "$pkgdir/usr/lib/gtk-phone-popup/gtk_popup.py"

  # Tiny wrapper for PATH
  install -Dm755 /dev/stdin "$pkgdir/usr/bin/gtk-phone-popup" <<'EOF'
#!/usr/bin/env bash
exec python3 /usr/lib/gtk-phone-popup/gtk_popup.py "$@"
EOF

  # User service to auto-start
  install -Dm644 "$srcdir/gtk-popup.service" \
    "$pkgdir/usr/lib/systemd/user/gtk-popup.service"
}