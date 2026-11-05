#!/bin/bash

# Chemin temporaire
WORKDIR="$HOME/RTKLIB_build"
REPO="https://github.com/rtklibexplorer/RTKLIB.git"

echo "📦 Cloning RTKLIB from $REPO..."
git clone "$REPO" "$WORKDIR"

# Compilation de convbin
echo "🔨 Building convbin..."
cd "$WORKDIR/app/consapp/convbin/gcc" || exit 1
make

# Compilation de rnx2rtkp
echo "🔨 Building rnx2rtkp..."
cd "$WORKDIR/app/consapp/rnx2rtkp/gcc" || exit 1
make

# Copie dans /usr/local/bin
echo "📁 Copying executables to /usr/local/bin..."
sudo cp "$WORKDIR/app/consapp/convbin/gcc/convbin" /usr/local/bin/
sudo cp "$WORKDIR/app/consapp/rnx2rtkp/gcc/rnx2rtkp" /usr/local/bin/

# Nettoyage (optionnel)
# echo "🧹 Cleaning up..."
# rm -rf "$WORKDIR"

echo "✅ Installation complete!"
echo "➡️  You can now run 'convbin' and 'rnx2rtkp' from any terminal."
