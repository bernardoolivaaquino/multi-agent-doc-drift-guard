# Unité de Télémétrie AeroSense (ATU-400) — Manuel Technique Utilisateur (FR)

## 1. Vue d'Ensemble du Système et Certification

L'**Unité de Télémétrie AeroSense (`ATU-400`)** est un module d'acquisition de données durci pour l'aéronautique et l'industrie, conçu pour la surveillance continue des structures et de l'avionique.

- **Norme de Certification :** `DO-160G / IP67`
- **Masse Totale de l'Unité :** `1.45 kg`
- **URL de Base API :** `https://api.aerosense.internal/v2`

## 2. Spécifications Électriques et d'Alimentation

L'`ATU-400` est alimenté par un bus avionique continu régulé. Vérifiez toujours la régulation de ligne avant la mise sous tension du faisceau.

| Paramètre | Valeur / Limite | Unité | Remarques |
| :--- | :--- | :--- | :--- |
| Tension Nominale d'Alimentation | `24.0` | `V DC` | Entrée bus régulée |
| Courant Continu Maximal | `2.5` | `A` | Protection par fusible |
| Rendement de l'Étage de Puissance | `0.92` | `ratio` | Convertisseur abaisseur synchrone |

La tension nominale d'alimentation est fixée à $V_{\text{nom}} = 24.0\,\text{V}$ pour un courant maximal $I_{\text{max}} = 2.5\,\text{A}$ et un rendement $\eta = 0.92$ :

$$P_{\text{max}} = V_{\text{nom}} \times I_{\text{max}} \times \eta$$

## 3. Limites Environnementales et Thermiques

Toutes les unités qualifiées doivent rester dans l'enveloppe thermique ambiante certifiée pendant les profils de mission.

| Paramètre | Valeur | Unité | Condition |
| :--- | :--- | :--- | :--- |
| Température Minimale de Fonctionnement | `-40.0` | `°C` | Démarrage à froid validé |
| Température Maximale de Fonctionnement | `70.0` | `°C` | Régime continu à pleine charge |

L'enveloppe thermique certifiée vérifie $-40.0\,^{\circ}\text{C} \le T_{\text{op}} \le 70.0\,^{\circ}\text{C}$ en convection naturelle.

## 4. Acquisition Télémétrique et Filtrage

Chaque voie analogique est numérisée de façon synchrone avant la mise en trame et l'envoi sur la liaison montante.

| Paramètre | Valeur | Unité | Remarques |
| :--- | :--- | :--- | :--- |
| Fréquence d'Échantillonnage | `500` | `Hz` | Capture multivoie synchrone |
| Résolution ADC | `16` | `bits` | Convertisseur SAR différentiel |

À la fréquence d'échantillonnage configurée $f_s = 500\,\text{Hz}$, la fréquence de coupure théorique de Nyquist s'exprime par :

$$f_{\text{Nyquist}} = \frac{f_s}{2}$$

## 5. Référence API REST

Les trames de télémétrie sont ingérées en HTTPS via le point de terminaison `POST /v2/telemetry/ingest`.

- **Point de Terminaison :** `POST /v2/telemetry/ingest`
- **Limite de Débit :** `120 req/min` par jeton d'équipement authentifié

### Paramètres de Requête

| Paramètre | Type | Requis | Défaut | Valeurs Autorisées | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `device_id` | `string` | `true` | `—` | `any serial` | Identifiant série matériel ATU unique |

## 6. Montage Mécanique et Maintenance

Fixez le châssis sur une cloison structurelle plane à l'aide de quatre vis M5 en acier inoxydable.

- **Couple de Serrage des Fixations :** `4.5 N·m`
- **Intervalle d'Étalonnage Programmé :** `12 months`
