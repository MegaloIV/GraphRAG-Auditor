# GraphRAG-Auditor

Sistema de auditoría semántica de documentos académicos que utiliza tecnología GraphRAG (Graph Retrieval-Augmented Generation) para validar consistencia entre citas, referencias bibliográficas y contenido. Detecta alucinaciones semánticas y distorsiones de información, asegurando que las referencias académicas mantengan fidelidad al contenido original.

## Descripción General

GraphRAG-Auditor automatiza el proceso de auditoría académica mediante:

- Procesamiento y extracción de documentos PDF
- Análisis semántico de citas y referencias
- Construcción de grafos de conocimiento (Neo4j)
- Validación de consistencia bibliográfica
- Detección de inconsistencias semánticas
- Interfaz visual para revisión de resultados

## Arquitectura de Carpetas

```
GraphRAG-Auditor/
├── backend/                          # Aplicación backend (Python/FastAPI)
│   ├── app/
│   │   ├── api/
│   │   │   └── routes/              # Definición de endpoints REST
│   │   │       ├── ingesta.py       # Rutas para procesamiento de documentos
│   │   │       └── grafo.py         # Rutas para consultas de grafos
│   │   ├── core/
│   │   │   ├── config.py            # Configuración centralizada (variables de entorno)
│   │   │   └── logging.py           # Setup de logging estructurado
│   │   ├── models/                  # Modelos de datos Pydantic
│   │   ├── services/                # Lógica de negocio
│   │   │   └── grafo/
│   │   │       ├── neo4j_service.py # Integración con Neo4j
│   │   │       └── rag_service.py   # Lógica GraphRAG
│   │   ├── utils/                   # Funciones utilitarias
│   │   └── main.py                  # Punto de entrada FastAPI
│   ├── data/                         # Almacenamiento de datos procesados
│   ├── logs/                         # Archivos de log
│   ├── tests/                        # Suite de pruebas unitarias
│   ├── scripts/                      # Scripts de utilidad
│   ├── requirements.txt              # Dependencias Python
│   └── .env                          # Variables de entorno
│
├── frontend/                         # Aplicación frontend (React/Vite)
│   ├── src/
│   │   ├── components/               # Componentes React reutilizables
│   │   ├── pages/                    # Páginas principales
│   │   ├── hooks/                    # Hooks personalizados
│   │   ├── api/                      # Cliente HTTP para backend
│   │   ├── store/                    # Gestión de estado
│   │   ├── styles/                   # Estilos globales
│   │   ├── assets/                   # Recursos estáticos
│   │   └── App.jsx                   # Componente raíz
│   ├── public/                       # Archivos públicos
│   ├── package.json                  # Dependencias JavaScript
│   ├── vite.config.js                # Configuración Vite
│   └── eslint.config.js              # Configuración ESLint
│
├── docs/                             # Documentación adicional
├── Keys/                             # Credenciales y configuración sensible
├── LICENSE
└── README.md
```

### Flujo de Procesamiento

1. **Ingesta de Documentos**
   - Usuario carga archivo PDF a través del frontend
   - Backend recibe y valida el archivo
   - Documento se almacena en directorio de uploads

2. **Extracción y Análisis**
   - Extracción de texto, metadatos y referencias del PDF
   - Normalización de contenido
   - Identificación de citas y referencias bibliográficas

3. **Construcción del Grafo de Conocimiento**
   - Creación de nodos para documentos, citas, referencias y entidades
   - Establecimiento de relaciones semánticas
   - Almacenamiento en base de datos Neo4j

4. **Validación Semántica**
   - Recuperación de fragmentos relevantes mediante embeddings
   - Comparación de contenido de citas con fuentes originales
   - Generación de puntuaciones de consistencia

5. **Presentación de Resultados**
   - Visualización del grafo de conocimiento
   - Reporte detallado de inconsistencias encontradas
   - Interfaz interactiva para exploración

## Tecnologías Utilizadas

### Backend

| Componente | Tecnología | Versión |
|-----------|-----------|---------|
| Framework Web | FastAPI | 0.115.5 |
| Servidor ASGI | Uvicorn | 0.32.1 |
| Validación de datos | Pydantic | 2.9.2 |
| Base de datos de grafos | Neo4j | 5.27.0 |
| Procesamiento de PDF | PyMuPDF, PyPDF2 | 1.24.13, 5.7.1 |
| Modelos de lenguaje | LangChain, Groq, Google Generative AI | Múltiples |
| Embeddings | Sentence Transformers | 5.4.1 |
| Bases de datos vectoriales | ChromaDB | 1.5.8 |
| Manipulación de grafos | NetworkX | 3.6.1 |
| Machine Learning | PyTorch, Scikit-learn | 2.11.0, 1.8.0 |
| Autenticación | BCrypt | 5.0.0 |
| Testing | Pytest | 8.3.3 |
| Logging | Structlog | 24.4.0 |

### Frontend

| Componente | Tecnología | Versión |
|-----------|-----------|---------|
| Framework UI | React | 19.2.5 |
| Build Tool | Vite | 8.0.10 |
| Enrutamiento | React Router | 7.14.2 |
| Cliente HTTP | Axios | 1.16.0 |
| Iconos | Lucide React | 1.14.0 |
| Gestión de archivos | React Dropzone | 15.0.0 |
| Linter | ESLint | 10.2.1 |

### Infraestructura y Dependencias

- **Python**: 3.10+
- **Node.js**: 18+
- **Neo4j**: 5.x
- **Docker**: Contenedores (opcional)

## Requisitos Previos

### Backend
```bash
python >= 3.10
pip install -r requirements.txt
```

### Frontend
```bash
node >= 18
npm install
```

### Variables de Entorno

Crear archivo `.env` en `backend/`:

```
APP_ENV=development
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
OPENAI_API_KEY=your_key
UPLOAD_DIR=./data/uploads
PROCESSED_DIR=./data/processed
```

## Uso

### Iniciar Backend
```bash
cd backend
z
```

### Iniciar Frontend
```bash
cd frontend
npm run dev
```

El servidor backend estará disponible en `http://localhost:8000`
El servidor frontend estará disponible en `http://localhost:5173`

## Endpoints Principales

- `GET /` - Estado del servidor
- `GET /health` - Verificación de salud
- `POST /api/v1/ingesta/upload` - Carga de documentos
- `GET /api/v1/grafo/visualizar` - Visualización del grafo
- `GET /api/v1/grafo/validar` - Validación de referencias

## Testing

```bash
cd backend
pytest
pytest --cov=app          # Con cobertura
```

