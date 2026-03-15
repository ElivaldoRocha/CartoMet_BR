# Changelog

Todas as mudanças notáveis deste projeto serão documentadas neste arquivo.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/).

## [1.2.2] - 2026-03-15

### Corrigido
- Erro "'NoneType' object has no attribute 'write'" ao executar em modo windowed
- Tratamento de erros de permissão ao salvar dados
- Validação de arquivos baixados do ECMWF

### Melhorado
- Mensagens de erro em português mais descritivas
- Configuração de certificados SSL para downloads

## [1.2.0] - 2026-03-14

### Adicionado
- Interface gráfica PyQt6 completa
- 10 simbologias meteorológicas (Frentes, ZCAS, ZCIT, Cavado, Crista, etc.)
- Regiões predefinidas (América do Sul, Brasil, Nordeste, Sudeste, Sul)
- Exportação em PNG, JPEG e PDF com 200 DPI
- Atalhos de teclado para todas as simbologias
- Painel de créditos com informações do desenvolvedor
- Barra de status com coordenadas e versão

### Melhorado
- Download automático de dados ECMWF Open Data
- Visualização de PNMM, Espessura 1000-500 hPa e Centros H/L
- Controle de suavização gaussiana (σ de 0 a 5)

## [1.0.0] - 2026-03-01

### Adicionado
- Versão inicial com funcionalidades básicas
- Download de dados ECMWF
- Visualização de campos meteorológicos
- Desenho interativo de frentes
