from fastapi import FastAPI

app = FastAPI(title='DC Build Planner')


@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'dc-build-planner'}
