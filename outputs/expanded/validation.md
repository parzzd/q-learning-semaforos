# Validación de la red ampliada

Resultado: **PASÓ** en simulación, con demanda sintética.

El modelo anterior no se modificó ni se volvió a entrenar. Se probaron reglas de asignación y verdes centrales fijos de 20 s.

## Red y extracción

Área OSM: `[-77.0665, -12.1075, -77.042, -12.0825]`. Descarga: 2026-10-05T02:45:06.883301+00:00.

1806 tramos; 152 puntos OSM con `highway=traffic_signals`; 38 controles importados, de los cuales 6 vecinos y el central están en los corredores activos.

Los puntos OSM pueden representar señales de aproximación o cruces. Se agrupan espacialmente y no equivalen uno a uno a intersecciones.

| Acceso | Quinta calle transversal | Distancia a quinta calle | Inicio de ruta | Semáforos previos |
| --- | --- | ---: | ---: | ---: |
| C1 · Salaverry · sur | Calle Augusto Bolognesi | 512.9 m | 645.7 m | 1 |
| C2 · Javier Prado · este | Calle Los Eucaliptos | 484.0 m | 781.8 m | 1 |
| C3 · Salaverry · norte | Jirón Huiracocha | 853.5 m | 1052.8 m | 2 |
| C4 · Javier Prado · oeste | Avenida Juan de Aliaga | 470.6 m | 595.8 m | 2 |

Cada ruta se inicia antes de un semáforo previo. Se cuentan cruces con calles con nombre, excluyendo nodos de geometría y accesos sin nombre. El acceso este necesita un sexto cruce y un segmento de margen para atravesar el semáforo de Las Flores.

12 rutas centrales y 10 rutas laterales tienen conexiones válidas para automóviles. Se mantienen cinco cámaras: su cobertura local no se amplía artificialmente.

## Semáforos vecinos del ensayo

| Control | Cruce según red OSM | Puntos OSM asociados | Plan |
| --- | --- | ---: | --- |
| N1 | Avenida Javier Prado Oeste | 4 | Generado por SUMO |
| N2 | Avenida Javier Prado Oeste / Avenida Las Flores | 4 | Generado por SUMO |
| N3 | Avenida Faustino Sanchez Carrión / Avenida General Salaverry | 4 | Generado por SUMO |
| N4 | Avenida Cádiz / Avenida Eduardo Avaroa / Avenida General Salaverry | 4 | Generado por SUMO |
| N5 | Avenida Alberto del Campo / Avenida General Salaverry | 4 | Generado por SUMO |
| N6 | Avenida Javier Prado Oeste / Calle Nicanor Rocca de Vergallo | 2 | Generado por SUMO |

## Pruebas SUMO

| Controlador | Semilla | Vehículos insertados / completados | Cruces centrales tras TLS previo | Cola central media | Colisiones / teleports | Pendientes de insertar |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| adaptive | 201 | 660 / 659 | 422 / 422 | 17.128 | 0 / 0 | 0 |
| fixed | 201 | 660 / 660 | 422 / 422 | 15.125 | 0 / 0 | 0 |
| adaptive | 202 | 692 / 689 | 473 / 473 | 25.308 | 0 / 0 | 0 |
| fixed | 202 | 692 / 692 | 473 / 473 | 22.232 | 0 / 0 | 0 |
| adaptive | 203 | 651 / 650 | 452 / 452 | 25.047 | 0 / 0 | 0 |
| fixed | 203 | 651 / 651 | 452 / 452 | 20.843 | 0 / 0 | 0 |

Se observaron 2694 cruces centrales en 6 ejecuciones de 1200 s. Cada uno tuvo un cruce semafórico previo registrado por sus transiciones reales entre carriles. Los cuatro accesos recibieron tráfico en cada ejecución.

Cola central media entre semillas: reglas **22.49 vehículos**, verde fijo **19.40 vehículos**. La demanda de cada pareja de controladores se comprobó idéntica byte a byte. El verde fijo es la referencia para evaluar el próximo entrenamiento.

Se verificaron las fases permitidas, tiempos de 10–60 s, igualdad de tiempos en sentidos opuestos, transiciones con despeje, estados de los 6 semáforos vecinos y al menos un control previo con vehículos detenidos ante rojo por acceso. Un semáforo puede recibir un pelotón completamente durante su verde; no se exige que todos detengan vehículos en cada semilla. Las llegadas por acceso se registran en ventanas de 10 s para revisar los pelotones.

## Revisar y decidir el entrenamiento

Abre [simulacion.html](simulacion.html): reglas y verde fijo usan la misma demanda para la primera semilla. El mapa permite zoom y arrastre, muestra la quinta cuadra y los estados/tiempos de N1–N6.

La validación técnica permite usar esta red como siguiente escenario de entrenamiento sintético. Antes de interpretarla como tráfico real, hay que contrastar en campo las señales, sentidos, carriles de giro y tiempos, y calibrar la demanda con conteos. El modelo anterior se bloquea para esta red porque corresponde a otro escenario.

## Límites de la validación

- La asociación de puntos OSM a controles es espacial, no una inspección física.
- Planes vecinos y conexiones de carriles generados por SUMO; no se dispone de tiempos municipales.
- Demanda sintética sin conteos locales, peatones, buses ni oclusiones de cámara.
- La prioridad de emergencias se conserva sólo en el cruce central; vecinos con ciclos fijos.
- Cinco cruces de calles por sentido, con margen; el acceso este añade un cruce para incluir TLS previo.
- No comparar estas colas directamente con el piloto corto: cambió la red y la demanda.

Fuentes: [OpenStreetMap y ODbL](https://www.openstreetmap.org/copyright), [importación y fases inferidas de SUMO](https://sumo.dlr.de/userdoc/Networks/Import/OpenStreetMap.html).
