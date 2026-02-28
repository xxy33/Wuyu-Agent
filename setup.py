"""
Wuyu-Agent (swagent) 安装配置
"""
from setuptools import setup, find_packages
import os


def read_file(filename):
    filepath = os.path.join(os.path.dirname(__file__), filename)
    if os.path.exists(filepath):
        with open(filepath, encoding='utf-8') as f:
            return f.read()
    return ""


setup(
    name="swagent",
    version="0.1.0",
    author="WuYu Team",
    author_email="wuyu@example.com",
    description="面向固体废物领域的多智能体协作框架",
    long_description=read_file('README.md'),
    long_description_content_type="text/markdown",
    url="https://github.com/xxy33/Wuyu-Agent",
    packages=find_packages(exclude=['tests', 'tests.*', 'examples', 'docs', 'web', 'web.*']),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Science/Research",
        "Intended Audience :: Developers",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.9",
    install_requires=[
        "pydantic>=2.0.0",
    ],
    extras_require={
        'llm': [
            'openai>=1.0.0',
            'tiktoken>=0.5.0',
        ],
        'web': [
            'fastapi>=0.100.0',
            'uvicorn>=0.22.0',
            'sse-starlette>=1.6.0',
            'python-multipart>=0.0.6',
            'jinja2>=3.1.0',
            'aiofiles>=23.0.0',
        ],
        'gis': [
            'earthengine-api>=0.1.300',
            'geemap>=0.30.0',
            'mercantile>=1.2.1',
            'Pillow>=10.0.0',
        ],
        'data': [
            'pandas>=2.0.0',
            'numpy>=1.24.0',
            'scipy>=1.10.0',
            'matplotlib>=3.7.0',
            'plotly>=5.14.0',
        ],
        'storage': [
            'redis>=4.5.0',
            'pymongo>=4.3.0',
        ],
        'vectors': [
            'chromadb>=0.4.0',
            'sentence-transformers>=2.2.0',
        ],
        'dev': [
            'pytest>=7.4.0',
            'pytest-asyncio>=0.21.0',
            'pytest-cov>=4.1.0',
            'black>=23.0.0',
            'flake8>=6.0.0',
            'mypy>=1.4.0',
            'isort>=5.12.0',
        ],
        'full': [
            'openai>=1.0.0',
            'tiktoken>=0.5.0',
            'aiohttp>=3.8.0',
            'requests>=2.31.0',
            'beautifulsoup4>=4.12.0',
            'lxml>=4.9.0',
            'pandas>=2.0.0',
            'numpy>=1.24.0',
            'scipy>=1.10.0',
            'matplotlib>=3.7.0',
            'plotly>=5.14.0',
            'Pillow>=10.0.0',
            'earthengine-api>=0.1.300',
            'geemap>=0.30.0',
            'mercantile>=1.2.1',
            'fastapi>=0.100.0',
            'uvicorn>=0.22.0',
            'sse-starlette>=1.6.0',
            'python-multipart>=0.0.6',
            'jinja2>=3.1.0',
            'aiofiles>=23.0.0',
            'colorlog>=6.7.0',
            'tqdm>=4.65.0',
            'pyyaml>=6.0',
            'python-dotenv>=1.0.0',
        ],
    },
    include_package_data=True,
    package_data={
        'swagent': [
            'prompts/templates/*.txt',
            'domain/data/*.json',
        ],
    },
    keywords=[
        'agent',
        'multi-agent',
        'llm',
        'solid-waste',
        'stategraph',
        'workflow',
    ],
    project_urls={
        'Bug Reports': 'https://github.com/xxy33/Wuyu-Agent/issues',
        'Source': 'https://github.com/xxy33/Wuyu-Agent',
    },
)
