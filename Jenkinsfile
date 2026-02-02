pipeline {
  agent any

  environment {
    REGISTRY = "image-registry.openshift-image-registry.svc:5000"
    IMAGE_NS = "ecomm"

    GITOPS_REPO = "https://github.com/printesh99/ecomm-cdc-gitops.git"
    GITOPS_BRANCH = "main"
    GITOPS_OVERLAY_PATH = "apps/overlays/dev/kustomization.yaml"
  }

  stages {
    stage("Checkout") {
      steps {
        checkout scm
        script {
          GIT_SHA = sh(script: "git rev-parse --short HEAD", returnStdout: true).trim()
          echo "GIT_SHA=${GIT_SHA}"
        }
      }
    }

    stage("oc login (in-cluster SA)") {
      steps {
        sh '''
          set -e
          TOKEN=$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)
          CACRT=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt
          oc login https://kubernetes.default.svc --token="$TOKEN" --certificate-authority="$CACRT" >/dev/null
          oc whoami
        '''
      }
    }

    stage("Build & Tag Images (Binary Builds)") {
      steps {
        script {
          def services = [
            [name:"catalog-service",  dir:"services/catalog"],
            [name:"cart-service",     dir:"services/cart"],
            [name:"orders-service",   dir:"services/orders"],
            [name:"payment-service",  dir:"services/payment"],
            [name:"shipping-service", dir:"services/shipping"]
          ]

          for (s in services) {
            sh """
              set -e
              echo "==> Building ${s.name} from ${s.dir}"

              # Create BuildConfig once
              oc -n ${IMAGE_NS} get bc/${s.name} >/dev/null 2>&1 || \\
                oc -n ${IMAGE_NS} new-build --name=${s.name} --strategy=docker --binary=true

              # Start binary build from folder
              oc -n ${IMAGE_NS} start-build ${s.name} --from-dir=${s.dir} --follow

              # Tag imagestream with git sha
              oc -n ${IMAGE_NS} tag ${s.name}:latest ${s.name}:${GIT_SHA}

              # show result
              oc -n ${IMAGE_NS} get is/${s.name} -o jsonpath='{.status.dockerImageRepository}{"\\n"}' || true
            """
          }
        }
      }
    }

    stage("Update GitOps (bump image tags)") {
      steps {
        withCredentials([usernamePassword(credentialsId: 'github-pat', usernameVariable: 'GIT_USER', passwordVariable: 'GIT_PAT')]) {
          sh '''
            set -e
            rm -rf /tmp/gitops && mkdir -p /tmp/gitops
            cd /tmp/gitops

            git clone ${GITOPS_REPO} repo
            cd repo
            git checkout ${GITOPS_BRANCH}

            # Replace newTag for each service (expects images: entries exist)
            for svc in catalog-service cart-service orders-service payment-service shipping-service; do
              echo "Updating tag for $svc to ${GIT_SHA}"
              perl -0777 -i -pe "s/(name:\\s*$svc\\s*\\n\\s*newName:.*\\n\\s*newTag:)\\s*.*$/\\1 ${GIT_SHA}/ms" ${GITOPS_OVERLAY_PATH} || true
            done

            git add ${GITOPS_OVERLAY_PATH}
            git commit -m "ci: bump images to ${GIT_SHA}" || echo "No changes to commit"
            git push https://${GIT_USER}:${GIT_PAT}@github.com/printesh99/ecomm-cdc-gitops.git ${GITOPS_BRANCH}
          '''
        }
      }
    }
  }
}

