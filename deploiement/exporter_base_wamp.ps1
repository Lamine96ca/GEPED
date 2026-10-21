# gePed — export de la base locale (WAMP) pour l'importer chez o2switch (phpMyAdmin ou SSH).
# Dans PowerShell, depuis le dossier du projet :   powershell -File deploiement\exporter_base_wamp.ps1
# Produit sauvegardes\pfe2_pour_o2switch_<date>.sql (fichier exclu de Git : il contient les données des étudiants).
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$mysqldump = Get-ChildItem 'C:\wamp64\bin\mysql\*\bin\mysqldump.exe' | Sort-Object FullName -Descending | Select-Object -First 1
if (-not $mysqldump) { throw "mysqldump introuvable dans C:\wamp64\bin\mysql : WAMP est-il installé ?" }
New-Item -ItemType Directory -Force sauvegardes | Out-Null
$fichier = "sauvegardes\pfe2_pour_o2switch_$(Get-Date -Format yyyyMMdd_HHmmss).sql"
# --no-tablespaces : évite une erreur de droits chez l'hébergeur ; pas de « CREATE DATABASE » (la base existe déjà là-bas)
& $mysqldump.FullName -u root --single-transaction --routines --no-tablespaces --default-character-set=utf8mb4 `
    --result-file=$fichier pfe2
if ($LASTEXITCODE -ne 0) { throw "L'export a échoué (WAMP est-il lancé ?)" }
$taille = [math]::Round((Get-Item $fichier).Length / 1MB, 1)
Write-Output "Export terminé : $fichier ($taille Mo). À importer dans la base o2switch (phpMyAdmin → Importer)."
