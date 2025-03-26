# Dataflödes- och forskningsdatahanteringssystem

Ett system för att bearbeta, validera, granska och berika forskningsdata från olika källor.

## Systemöversikt

Systemet implementerar följande dataflöde:

```
A[Excel-fil från olika källor] --> B[Pandas DataFrame]
B --> |Validering & normalisering| C[Staging-databas]
C --> D[Webbgränssnitt - granskning & städning]
D -->|Godkänd data flyttas| E[Permanent databas]
E --> F[Extern data: PubMed, Google Scholar, sociala medier]
F --> |API-calls & scraping| G[Automatiserad datainhämtning]
G --> |Integreras & matchas via ORCID| C
```

## Installation

1. Klona detta repository:
```
git clone <repository-url>
cd <repository-directory>
```

2. Skapa en virtuell miljö och installera beroenden:
```
python -m venv venv
venv\Scripts\activate  # På Windows
pip install -r requirements.txt
```

3. Skapa nödvändiga kataloger:
```
python src/main.py --setup
```

## Användning

### 1. Bearbeta Excel-filer till staging-databasen

Lägg Excel-filer i katalogen `data/raw` och kör sedan:

```
python src/main.py --process-excel
```

Du kan också ange en annan katalog:

```
python src/main.py --process-excel --excel-dir /sökväg/till/excelfiler
```

### 2. Starta webbgränssnittet för att granska och redigera data

```
python src/web_interface/app.py
```

Öppna sedan webbläsaren och gå till `http://localhost:5000/` för att komma åt gränssnittet.

### 3. Godkänn och flytta data till permanent databas

Via webbgränssnittet kan du godkänna data, eller via kommandoraden:

```
python src/main.py --approve-dataset <dataset-id>
```

### 4. Matcha forskare mot ORCID-ID

```
python src/main.py --match-orcid --name-column "forskare_namn" --keywords-column "forskningsområden" --institution-column "institution"
```

### 5. Hämta extern data från PubMed

```
python src/main.py --collect-pubmed --query "genomics AND cancer" --max-results 20
```

Eller baserat på ORCID:

```
python src/main.py --collect-pubmed --orcid "0000-0001-2345-6789" --max-results 20
```

## Systemkomponenter

### Data Processing

- `excel_to_dataframe.py` - Läser Excel-filer och konverterar till Pandas DataFrames

### Databas

- `staging_db.py` - Staging-databas för validering och mellanlagring av data
- `permanent_db.py` - Permanent databas för godkänd data

### Webbgränssnitt

- `app.py` - Flask-app för granskning och redigering av data

### Extern Datainsamling

- `data_collector.py` - Klasser för att interagera med externa API:er (PubMed, Google Scholar, ORCID)

## Konfiguration

Valideringsregler kan definieras i en JSON-fil i `config/validation_rules.json` med följande format:

```json
{
  "schema_name": {
    "column_name": {
      "type": "str|int|float|date",
      "required": true|false,
      "unique": true|false
    }
  }
}
```

## Licens

Detta projekt är licensierat under [MIT-licensen](LICENSE). 